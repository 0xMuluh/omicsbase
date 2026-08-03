"""Phase 5 Test Suite: State Machine & Concurrent Session Integrity.

Verifies status transition validations for Project.status and atomic database updates
in background tasks.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Job, Project
from app.tasks.analysis import (
    ALLOWED_TRANSITIONS,
    _update_job,
    validate_status_transition,
)

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_valid_status_transitions():
    assert validate_status_transition("created", "planning") is True
    assert validate_status_transition("planning", "planned") is True
    assert validate_status_transition("planned", "approved") is True
    assert validate_status_transition("approved", "generating") is True
    assert validate_status_transition("generating", "rendering") is True
    assert validate_status_transition("rendering", "repairing") is True
    assert validate_status_transition("repairing", "rendering") is True
    assert validate_status_transition("rendering", "reviewing") is True
    assert validate_status_transition("reviewing", "completed") is True
    assert validate_status_transition("rendering", "failed") is True


def test_invalid_status_transitions():
    assert validate_status_transition("created", "reviewing") is False
    assert validate_status_transition("created", "completed") is False
    assert validate_status_transition("completed", "reviewing") is False
    assert validate_status_transition("completed", "repairing") is False


def test_job_update_integrity(db_session):
    project = Project(name="Phase 5 Test", status="created", owner_id="user_1", tenant_id="tenant_1")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    job = Job(project_id=project.id, job_type="render", status="queued")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    _update_job(db_session, str(job.id), status="running", progress=[{"step": "rendering", "status": "running"}])

    updated_job = db_session.query(Job).filter(Job.id == job.id).first()
    assert updated_job.status == "running"
    assert len(updated_job.progress) == 1
    assert updated_job.progress[0]["step"] == "rendering"
