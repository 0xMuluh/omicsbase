"""Regression coverage for autonomous linear NoteThread turns."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent_test_helpers import text_turn, tool_turn
from app.database import Base, get_db
from app.main import app
from app.models.notes import CellExecution, NoteCell, NoteCellRevision, NoteThread
from app.models.runs import AgentRun, RunTelemetry
from app.services import note_agent


def test_note_agent_preserves_native_tool_history(monkeypatch):
    provider_calls = []

    async def fake_stream(**kwargs):
        provider_calls.append(kwargs["messages"])
        events = tool_turn(
            "run_r_cell",
            {"code": "mean(c(1, 2, 3))", "purpose": "Check the mean"},
        ) if len(provider_calls) == 1 else text_turn("The queued cell will provide the calculation.")
        for event in events:
            yield event

    monkeypatch.setattr("app.services.agent_core.stream_llm_with_tools", fake_stream)

    async def judge_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_needs_tools)

    async def action_handler(_name, _arguments):
        return {
            "status": "ok",
            "turn_id": "turn-1",
            "cell": {"id": "cell-1"},
            "execution": {"id": "execution-1", "status": "queued"},
        }

    async def collect():
        return [
            event
            async for event in note_agent.stream_note_agent(
                message="Calculate the mean",
                cells=[],
                context={"scope": "standalone", "cells": []},
                action_handler=action_handler,
            )
        ]

    events = asyncio.run(collect())
    event_types = [event["type"] for event in events]
    assert "note_cell" in event_types
    assert "execution_queued" in event_types
    assert "wait" in event_types
    assert event_types[-1] == "final"
    assert len(provider_calls) == 1
    started = next(event for event in events if event["type"] == "tool_started")
    assert started["message"] == "Running R cell"
    assert "run_r_cell" not in str(started.get("message"))


def _setup_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return engine, testing_session


def test_demonstration_fast_path_persists_grounding_citations(monkeypatch, tmp_path):
    engine, testing_session = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_demo", "X-User-ID": "user_demo"}

    async def fake_stream(**kwargs):
        yield "A grounded demonstration is ready."

    def fake_search(*args, **kwargs):
        return {
            "status": "ok",
            "matches": [
                {
                    "citation": "OSCA, Demonstration methods (RELEASE_3_23, demo123)",
                    "book_title": "OSCA",
                    "heading_path": ["Demonstrations"],
                    "prose": "Use a seeded example when illustrating a method.",
                }
            ],
        }

    monkeypatch.setattr("app.services.intent_fastpath.stream_llm_text", fake_stream)
    monkeypatch.setattr("app.services.bioc_knowledge.search_bioc_knowledge", fake_search)
    monkeypatch.setattr(note_agent.settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(note_agent.settings, "fast_path_enabled", True)

    try:
        created = client.post("/api/notes", headers=headers, json={"title": "Untitled note"})
        assert created.status_code == 201
        thread_id = created.json()["id"]

        response = client.post(
            f"/api/notes/{thread_id}/turn",
            headers=headers,
            json={"message": "show me an example of a t-test"},
        )
        assert response.status_code == 200

        verify_db = testing_session()
        markdown_revisions = [
            revision
            for cell in verify_db.query(NoteCell).all()
            for revision in cell.revisions
            if revision.cell_type == "markdown"
        ]
        assert markdown_revisions
        assert markdown_revisions[-1].revision_metadata["knowledge_sources"] == [
            "OSCA, Demonstration methods (RELEASE_3_23, demo123)"
        ]
        verify_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_wait_for_note_execution_returns_terminal_payload():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api import projects_notes

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    thread = NoteThread(id=str(uuid.uuid4()), title="Wait note", thread_type="note")
    cell = NoteCell(id=str(uuid.uuid4()), thread_id=str(thread.id))
    revision = NoteCellRevision(
        id=str(uuid.uuid4()),
        cell_id=str(cell.id),
        revision=1,
        cell_type="code",
        content="1 + 1",
    )
    execution = CellExecution(
        id=str(uuid.uuid4()),
        revision_id=str(revision.id),
        attempt=1,
        status="completed",
        result_metadata={"stdout_preview": "42\n"},
    )
    session.add_all([thread, cell, revision, execution])
    session.commit()

    payload = asyncio.run(
        projects_notes._wait_for_note_execution(session, str(execution.id), timeout_seconds=30, cancel_check=None)
    )
    assert payload["status"] == "completed"
    assert payload["result_metadata"]["stdout_preview"] == "42\n"
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_standalone_turn_receives_terminal_cell_result(monkeypatch, tmp_path):
    engine, testing_session = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    provider_calls = []

    async def fake_stream(**kwargs):
        provider_calls.append(kwargs["messages"])
        events = tool_turn(
            "run_r_cell",
            {"code": "cat(6 * 7)", "purpose": "Compute"},
        ) if len(provider_calls) == 1 else text_turn("The cell above computed the answer.")
        for event in events:
            yield event

    async def fake_wait(db, execution_id, timeout_seconds, cancel_check=None):
        return {
            "id": execution_id,
            "status": "completed",
            "result_metadata": {"stdout_preview": "42\n"},
            "artifacts": [],
        }

    monkeypatch.setattr("app.services.agent_core.stream_llm_with_tools", fake_stream)
    monkeypatch.setattr("app.api.projects_note_executions._dispatch_standalone", lambda *args: None)
    monkeypatch.setattr("app.api.projects_notes._wait_for_note_execution", fake_wait)
    from app.config import settings

    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))

    try:
        from app.config import settings

        monkeypatch.setattr(settings, "fast_path_enabled", False)
        created = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Untitled note"},
        )
        thread_id = created.json()["id"]
        response = client.post(
            f"/api/notes/{thread_id}/turn",
            headers=headers,
            json={"message": "What is 6 times 7?"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        queued = [event for event in events if event["type"] == "execution_queued"]
        assert queued, "expected an execution event"
        execution = queued[-1]["execution"]
        assert execution["status"] == "completed"
        assert execution["result_metadata"]["stdout_preview"] == "42\n"
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_standalone_turn_persists_user_code_execution_and_answer(monkeypatch, tmp_path):
    engine, testing_session = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    provider_calls = []

    async def fake_stream(**kwargs):
        provider_calls.append(kwargs["messages"])
        events = tool_turn(
            "run_r_cell",
            {"code": "mean(c(1, 2, 3))", "purpose": "Calculate the requested mean"},
        ) if len(provider_calls) == 1 else text_turn("The mean is queued for execution.")
        for event in events:
            yield event

    monkeypatch.setattr("app.services.agent_core.stream_llm_with_tools", fake_stream)
    monkeypatch.setattr("app.api.projects_note_executions._dispatch_standalone", lambda *args: None)
    from app.config import settings

    async def judge_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_needs_tools)
    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(settings, "note_execution_agent_wait_enabled", False)

    try:
        created = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Untitled note"},
        )
        assert created.status_code == 201
        thread_id = created.json()["id"]

        response = client.post(
            f"/api/notes/{thread_id}/turn",
            headers=headers,
            json={"message": "Calculate the mean of 1, 2, and 3"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        assert [event["type"] for event in events].count("note_cell") >= 2
        assert any(event["type"] == "execution_queued" for event in events)
        final_event = next(event for event in events if event["type"] == "final")
        assert final_event["cell"]["revisions"][0]["cell_type"] == "markdown"
        assert events[-1]["type"] in {"final", "paused"}

        detail = client.get(f"/api/notes/{thread_id}", headers=headers)
        assert detail.status_code == 200
        persisted_code = detail.json()["cells"][1]
        assert persisted_code["latest_execution"]["status"] == "queued"

        verify_db = testing_session()
        run_id = next(event["run"]["id"] for event in events if event["type"] == "run")
        assert verify_db.query(AgentRun).filter(AgentRun.id == run_id).one().status == "paused"
        cells = verify_db.query(NoteCell).order_by(NoteCell.position.asc()).all()
        assert [cell.revisions[-1].cell_type for cell in cells] == ["agent", "code", "markdown"]
        assert cells[0].revisions[0].content == "Calculate the mean of 1, 2, and 3"
        assert cells[1].revisions[0].revision_metadata["generated_by"] == "note_agent"
        execution = verify_db.query(CellExecution).one()
        assert execution.status == "queued"
        assert len(provider_calls) == 1
        verify_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_standalone_turn_interleaves_notes_and_code_cells(monkeypatch, tmp_path):
    engine, testing_session = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    provider_calls = []

    async def fake_stream(**kwargs):
        provider_calls.append(kwargs["messages"])
        call_number = len(provider_calls)
        if len(provider_calls) == 1:
            events = tool_turn("add_note", {"text": "First we load the required libraries."}, call_id="call-1")
        elif len(provider_calls) == 2:
            events = tool_turn(
                "run_r_cell",
                {"code": "library(phyloseq)\nprint(1 + 1)", "purpose": "Prepare the analysis"},
                call_id="call-2",
            )
        else:
            events = text_turn("The notebook above explains each step and the cell is queued.")
        yield {
            "type": "usage",
            "usage": {
                "input_tokens": 10 * call_number,
                "output_tokens": call_number,
                "total_tokens": 11 * call_number,
            },
        }
        for event in events:
            yield event

    monkeypatch.setattr("app.services.agent_core.stream_llm_with_tools", fake_stream)
    monkeypatch.setattr("app.api.projects_note_executions._dispatch_standalone", lambda *args: None)
    from app.config import settings

    # The judge must not hit a live provider in tests: this message needs the
    # tool loop, so pin the judge decision.
    async def judge_returns_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_returns_needs_tools)
    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(settings, "note_execution_agent_wait_enabled", False)

    try:
        created = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Untitled note"},
        )
        thread_id = created.json()["id"]
        response = client.post(
            f"/api/notes/{thread_id}/turn",
            headers=headers,
            json={"message": "How do I compute alpha diversity?"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        note_events = [event for event in events if event["type"] == "note_cell"]
        assert any(event["cell"]["revisions"][0]["cell_type"] == "markdown" for event in note_events)
        assert any(event["type"] == "execution_queued" for event in events)

        verify_db = testing_session()
        cells = verify_db.query(NoteCell).order_by(NoteCell.position.asc()).all()
        types = [cell.revisions[-1].cell_type for cell in cells]
        assert types == ["agent", "markdown", "code", "markdown"]
        assert cells[1].revisions[0].content == "First we load the required libraries."
        assert cells[1].revisions[0].revision_metadata["generated_by"] == "note_agent"
        telemetry = (
            verify_db.query(RunTelemetry)
            .filter(RunTelemetry.operation == "note_turn")
            .one()
        )
        assert telemetry.input_tokens == 30
        assert telemetry.output_tokens == 3
        assert telemetry.total_tokens == 33
        assert telemetry.telemetry_metadata["provider_usage"]["total_tokens"] == 33
        verify_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_note_turn_streams_token_chunks_to_client(monkeypatch, tmp_path):
    engine, testing_session = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    answer = (
        "Streaming answer body with enough text to cross the chunk flush "
        "threshold multiple times so the client receives incremental chunks "
        "instead of one burst at the end of the turn. " * 2
    )

    async def fake_stream(**kwargs):
        # A real provider emits many small deltas; each flushes a ~96-char
        # token chunk to the durable stream.
        for i in range(0, len(answer), 30):
            yield {"type": "text_delta", "content": answer[i:i + 30]}
        yield {"type": "done"}

    monkeypatch.setattr("app.services.agent_core.stream_llm_with_tools", fake_stream)
    from app.config import settings

    async def judge_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_needs_tools)
    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))

    try:
        created = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Untitled note"},
        )
        thread_id = created.json()["id"]
        response = client.post(
            f"/api/notes/{thread_id}/turn",
            headers=headers,
            json={"message": "Calculate the mean of 1, 2, and 3"},
        )
        assert response.status_code == 200
        assert response.headers.get("x-agent-run-transport") == "durable-replay"
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        token_events = [event for event in events if event["type"] == "token"]
        assert len(token_events) >= 2, "client should receive multiple incremental token events"
        streamed = "".join(str(event.get("token") or "") for event in token_events)
        assert streamed == answer
        assert any(event["type"] == "final" for event in events)
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_note_judge_bridges_knowledge_with_runnable_example_to_agent_loop(monkeypatch):
    """needs_knowledge with a runnable book example routes to the agent loop
    so the worked example can actually run."""
    from app.services import intent_fastpath
    from app.services import note_agent as na_module

    executor = na_module.NoteAgentExecutor(
        message="explain multiple testing",
        cells=[],
        context={},
        knowledge_search_handler=lambda args: {
            "matches": [{"book_title": "OMA", "code": "p.adjust(x, method='BH')", "prose": "x", "citation": "c"}]
        },
    )

    async def fake_classify(message):
        return "needs_knowledge"

    monkeypatch.setattr(intent_fastpath, "classify_intent", fake_classify)
    intent = await executor.judge_intent("explain multiple testing")
    assert intent == "needs_tools"


@pytest.mark.asyncio
async def test_note_judge_keeps_conceptual_without_runnable_example(monkeypatch):
    from app.services import intent_fastpath
    from app.services import note_agent as na_module

    executor = na_module.NoteAgentExecutor(
        message="what is a p-value?",
        cells=[],
        context={},
        knowledge_search_handler=lambda args: {
            "matches": [{"book_title": "OMA", "prose": "no code here", "citation": "c"}]
        },
    )

    async def fake_classify(message):
        return "conceptual"

    monkeypatch.setattr(intent_fastpath, "classify_intent", fake_classify)
    intent = await executor.judge_intent("what is a p-value?")
    assert intent == "conceptual"
