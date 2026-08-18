"""Tests for shared durable AgentRun state, idempotency, replay, and telemetry."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models.project  # noqa: F401
import app.models.notes  # noqa: F401
from app.models.runs import AgentRun, RunEvent, RunTelemetry
from app.services.agent_runs import (
    IdempotencyConflict,
    append_run_event,
    create_or_get_agent_run,
    list_run_events,
    record_run_telemetry,
    request_run_cancel,
    transition_agent_run,
)


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def test_run_is_idempotent_and_replayable():
    engine, factory = _db()
    db = factory()
    try:
        payload = {"message": "inspect normalization", "chat_mode": "discuss"}
        run, created = create_or_get_agent_run(
            db,
            tenant_id="tenant-a",
            owner_id="user-a",
            surface="workspace",
            idempotency_scope="workspace:project-a:turn",
            idempotency_key="turn-1",
            request_payload=payload,
            project_id=None,
        )
        assert created is True
        db.commit()

        same_run, created_again = create_or_get_agent_run(
            db,
            tenant_id="tenant-a",
            owner_id="user-a",
            surface="workspace",
            idempotency_scope="workspace:project-a:turn",
            idempotency_key="turn-1",
            request_payload=payload,
        )
        assert created_again is False
        assert same_run.id == run.id

        transition_agent_run(db, run, "running", event_type="run_started")
        append_run_event(db, run, "tool_completed", {"tool": "inspect_project"}, idempotency_key="tool-1")
        append_run_event(db, run, "tool_completed", {"tool": "inspect_project"}, idempotency_key="tool-1")
        transition_agent_run(db, run, "completed", event_type="run_completed")
        record_run_telemetry(
            db,
            run,
            kind="agent",
            operation="agent_turn",
            duration_ms=12.5,
            input_tokens=10,
            output_tokens=20,
            provider="test",
            model="test-model",
        )
        db.commit()

        assert db.query(RunEvent).filter(RunEvent.run_id == run.id).count() == 4
        assert db.query(RunTelemetry).filter(RunTelemetry.run_id == run.id).one().total_tokens == 30
        assert len(list_run_events(db, run.id, after_sequence=1)) == 3
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_idempotency_conflict_and_cancel_are_safe():
    engine, factory = _db()
    db = factory()
    try:
        run, _ = create_or_get_agent_run(
            db,
            tenant_id="tenant-a",
            owner_id="user-a",
            surface="notes",
            idempotency_scope="notes:thread-a:turn",
            idempotency_key="turn-1",
            request_payload={"message": "one"},
            note_thread_id=None,
        )
        db.commit()

        try:
            create_or_get_agent_run(
                db,
                tenant_id="tenant-a",
                owner_id="user-a",
                surface="notes",
                idempotency_scope="notes:thread-a:turn",
                idempotency_key="turn-1",
                request_payload={"message": "different"},
            )
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("different payload must not reuse an idempotency key")

        request_run_cancel(db, run)
        db.commit()
        assert run.status == "cancelled"
        request_run_cancel(db, run)
        db.commit()
        assert run.status == "cancelled"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)



