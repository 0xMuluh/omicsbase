from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models.notes  # noqa: F401
import app.models.project  # noqa: F401
from app.models.runs import AgentRun
from app.services.agent_plans import (
    READY,
    attach_continuation_plan,
    build_continuation_plan,
    get_continuation_plan,
    mark_dependency_complete,
)
from app.services.agent_runs import create_or_get_agent_run, list_run_events


def test_async_dependency_promotes_one_durable_continuation():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = factory()
    try:
        run, created = create_or_get_agent_run(
            db,
            tenant_id="tenant-a",
            owner_id="user-a",
            surface="workspace",
            idempotency_scope="workspace:project-a:turn",
            idempotency_key="turn-1",
            request_payload={"message": "run the analysis"},
        )
        assert created is True
        plan = build_continuation_plan(
            run,
            action="run_analysis",
            dependency_kind="job",
            dependency_id="job-1",
            instruction="Run the analysis",
            dependency_status="queued",
        )
        attach_continuation_plan(run, plan)
        db.commit()

        assert mark_dependency_complete(
            db,
            dependency_kind="job",
            dependency_id="job-1",
            dependency_status="completed",
            result={"status": "completed", "rows": 12},
        ) == 1
        db.commit()
        db.expire_all()

        stored_run = db.query(AgentRun).filter(AgentRun.id == run.id).one()
        stored = get_continuation_plan(stored_run)
        assert stored is not None
        assert stored["status"] == READY
        assert stored["dependency_result"]["rows"] == 12

        events = list_run_events(db, str(run.id), after_sequence=0, limit=20)
        continuation_events = [event for event in events if event.get("event_type") == "continuation_ready"]
        assert len(continuation_events) == 1
        assert mark_dependency_complete(
            db,
            dependency_kind="job",
            dependency_id="job-1",
            dependency_status="completed",
            result={"status": "completed"},
        ) == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
