"""Focused coverage for claiming and dispatching durable continuations."""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models.notes  # noqa: F401
import app.models.project  # noqa: F401
from app.models.runs import AgentRun
from app.services.agent_continuations import dispatch_ready_continuations
from app.services.agent_plans import (
    DONE,
    attach_continuation_plan,
    build_continuation_plan,
    get_continuation_plan,
    mark_continuation_consumed,
)
from app.services.agent_runs import create_or_get_agent_run, list_run_events


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_ready_plan_is_claimed_once_before_dispatch(monkeypatch):
    engine, factory = _session_factory()
    db = factory()
    dispatched: list[str] = []
    try:
        run, _ = create_or_get_agent_run(
            db,
            tenant_id="tenant-a",
            owner_id="user-a",
            surface="workspace",
            idempotency_scope="workspace:project-a:turn",
            idempotency_key="turn-1",
            request_payload={"message": "run the analysis"},
        )
        plan = build_continuation_plan(
            run,
            action="run_analysis",
            dependency_kind="job",
            dependency_id="job-1",
            instruction="Run the analysis",
            dependency_status="completed",
        )
        attach_continuation_plan(run, plan)
        db.commit()
        monkeypatch.setattr(
            "app.services.agent_continuations._dispatch_continuation_worker",
            lambda run_id: dispatched.append(run_id),
        )

        assert dispatch_ready_continuations(db, dependency_kind="job", dependency_id="job-1") == 1
        assert dispatch_ready_continuations(db, dependency_kind="job", dependency_id="job-1") == 0
        db.expire_all()
        stored = db.query(AgentRun).filter(AgentRun.id == run.id).one()
        assert get_continuation_plan(stored)["status"] == "running"
        assert dispatched == [str(run.id)]
        assert any(
            event["event_type"] == "continuation_dispatched"
            for event in list_run_events(db, str(run.id), after_sequence=0, limit=20)
        )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_consumed_plan_is_no_longer_resumable():
    run = SimpleNamespace(
        id="run-1",
        surface="notes",
        project_id=None,
        note_thread_id="thread-1",
        run_metadata={},
        resumable=False,
    )
    plan = build_continuation_plan(
        run,
        action="run_r_cell",
        dependency_kind="execution",
        dependency_id="execution-1",
        instruction="Calculate the mean",
        dependency_status="completed",
    )
    attach_continuation_plan(run, plan)

    consumed = mark_continuation_consumed(run)

    assert consumed["status"] == DONE
    assert run.resumable is False
