"""Stage-aware retry behavior for failed pipeline jobs."""

from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import projects_pipeline
from app.database import Base
import app.models.notes  # noqa: F401
import app.models.runs  # noqa: F401
from app.models.project import Job, Project
from app.services import agent_runtime


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def _failed_project(db, *, failed_job_type: str) -> Project:
    project = Project(
        name=f"Failed {failed_job_type}",
        question="Compare groups",
        tenant_id="default_tenant",
        owner_id="default_user",
        status="failed",
        analysis_plan={"domain": "microbiome", "workflow": []},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    db.add(Job(project_id=str(project.id), job_type=failed_job_type, status="failed"))
    db.commit()
    return project


def test_failed_generation_can_retry_without_reapproval(monkeypatch):
    engine, factory = _database()
    db = factory()
    dispatched = []
    monkeypatch.setattr(
        projects_pipeline,
        "_dispatch_task",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(agent_runtime, "set_agent_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "record_agent_action", lambda *args, **kwargs: None)
    try:
        project = _failed_project(db, failed_job_type="generate")
        job = projects_pipeline.start_generation(
            str(project.id),
            BackgroundTasks(),
            db=db,
            tenant_id="default_tenant",
        )

        db.refresh(project)
        assert job.job_type == "generate"
        assert project.status == "generating"
        assert len(dispatched) == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_render_failure_cannot_be_retried_as_generation(monkeypatch):
    engine, factory = _database()
    db = factory()
    try:
        project = _failed_project(db, failed_job_type="render")
        with pytest.raises(HTTPException, match="retry the failed pipeline stage") as caught:
            projects_pipeline.start_generation(
                str(project.id),
                BackgroundTasks(),
                db=db,
                tenant_id="default_tenant",
            )
        assert caught.value.status_code == 409
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
