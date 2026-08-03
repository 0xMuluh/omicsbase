"""Tests for the persistent tool-using workspace agent loop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workspace_agent


def _project(tmp_path):
    project_dir = tmp_path / "project"
    results_dir = project_dir / "output" / "results"
    results_dir.mkdir(parents=True)
    (project_dir / "code").mkdir()
    (project_dir / "code" / "index.qmd").write_text("# Report\n")
    (results_dir / "alpha_diversity.csv").write_text(
        "sample_id,shannon,group\nS1,2.1,Control\nS2,3.2,Treatment\n"
    )
    return SimpleNamespace(
        id="project-1",
        name="Agent test",
        question="Compare groups",
        notes=None,
        status="completed",
        agent_state="completed",
        agent_memory={"summary": "Report ready"},
        agent_actions=[],
        study_manifest={"status": "ready", "domain": "microbiome"},
        analysis_plan={
            "project_name": "Agent test",
            "study_type": "two_group_comparison",
            "question": "Compare groups",
            "grouping_variable": "group",
            "group_levels": ["Control", "Treatment"],
            "workflow": [],
        },
        project_dir=str(project_dir),
        files=[],
    )


@pytest.mark.asyncio
async def test_agent_inspects_result_table_before_answering(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tool","tool":"read_results","arguments":{"path":"output/results/alpha_diversity.csv"},"reason":"Read the requested result table"}',
            '{"type":"final","message":"Treatment has the higher observed Shannon value in this two-row table."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    request = SimpleNamespace(
        message="Which group has higher Shannon diversity?",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    types = [event["type"] for event in events]
    assert types[0] == "status"
    assert "tool_started" in types
    assert "tool_completed" in types
    assert "action_event" in types
    assert events[-1]["type"] == "final"
    assert events[-1]["message"].startswith("Treatment")
    assert any(event["type"] == "token" for event in events)


@pytest.mark.asyncio
async def test_agent_falls_back_to_verified_edit_action(tmp_path, monkeypatch):
    async def unavailable_llm(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(workspace_agent, "call_llm", unavailable_llm)
    request = SimpleNamespace(
        message="Add a caption to the alpha diversity figure",
        selected_file="code/index.qmd",
        selected_content="# Unsaved report\n",
        selected_content_dirty=True,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    assert events[-1]["type"] == "action"
    assert events[-1]["action"] == "edit_project"
    assert "rerender" in events[-1]["message"]


@pytest.mark.asyncio
async def test_agent_streams_final_message_tokens(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        return '{"type":"final","message":"Shannon is higher in treatment."}'

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    request = SimpleNamespace(
        message="Summarize Shannon",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert any(event["type"] == "token" for event in events)
    assert events[-1]["type"] == "final"
    assert events[-1]["message"] == "Shannon is higher in treatment."


@pytest.mark.asyncio
async def test_agent_uses_run_r_before_answering_package_question(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tool","tool":"run_r","arguments":{"code":"data.frame(a=1:2,b=c(\\"x\\",\\"y\\"))"},"reason":"Inspect example frame"}',
            '{"type":"final","message":"The frame has columns a and b."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    def fake_run_r(code, *, cwd=None, timeout_s=30):
        assert "data.frame" in code
        return {
            "status": "ok",
            "stdout": "",
            "stderr": "",
            "summary": {"class": "data.frame", "rows": 2, "columns": 2, "colnames": ["a", "b"], "head": ["1 | x", "2 | y"]},
        }

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    monkeypatch.setattr("app.services.r_inspect.run_r_inspect", fake_run_r)

    request = SimpleNamespace(
        message="What colnames does that package dataset have?",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    tool_started = next(event for event in events if event["type"] == "tool_started")
    tool_completed = next(event for event in events if event["type"] == "tool_completed")
    assert tool_started["tool"] == "run_r"
    assert "2×2" in tool_completed["summary"]
    assert events[-1]["type"] == "final"
    assert "a and b" in events[-1]["message"]


@pytest.mark.asyncio
async def test_discuss_mode_blocks_mutation_actions(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        assert "Discuss mode" in kwargs["system_prompt"] or "discuss" in kwargs["system_prompt"].lower()
        return (
            '{"type":"action","action":"edit_project","instruction":"change caption",'
            '"message":"I will edit the caption"}'
        )

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    request = SimpleNamespace(
        message="Change the alpha caption",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="discuss",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert events[-1]["type"] == "final"
    assert "The Plan" in events[-1]["message"]
    assert events[-1]["quick_actions"][0]["type"] == "implement"
    assert not any(event["type"] == "action" for event in events)


@pytest.mark.asyncio
async def test_agent_fast_path_greetings(tmp_path):
    request = SimpleNamespace(
        message="hello",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert events[-1]["type"] == "final"
    assert "ready to assist" in events[-1]["message"]


@pytest.mark.asyncio
async def test_agent_multi_tool_batching(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tools","tools":[{"tool":"read_file","arguments":{"path":"code/index.qmd"}},{"tool":"read_results","arguments":{"path":"output/results/alpha_diversity.csv"}}],"reason":"Inspect both report and result table"}',
            '{"type":"final","message":"Both files inspected."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    request = SimpleNamespace(
        message="Inspect report and results",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    completed_tools = [e["tool"] for e in events if e["type"] == "tool_completed"]
    assert completed_tools == ["read_file", "read_results"]
    assert events[-1]["type"] == "final"
    assert events[-1]["message"] == "Both files inspected."

