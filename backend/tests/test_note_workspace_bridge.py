"""Note-to-workspace bridge: promote_to_workspace and result artifact discovery."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.notes import NoteThread
from app.models.project import Project
from app.api.projects_notes import _promote_cell_to_workspace
from app.services.workspace_agent import list_project_result_artifacts

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


@pytest.fixture
def project_with_dir(tmp_path, db_session):
    project = Project(
        name="Bridge test",
        question="test",
        status="completed",
        tenant_id="default_tenant",
        owner_id="default_user",
        project_dir=str(tmp_path),
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    (tmp_path / "code").mkdir()
    return project


def _thread(db_session, project, **kwargs):
    thread = NoteThread(
        title="Notebook",
        thread_type="notebook",
        tenant_id="default_tenant",
        owner_id="default_user",
        project_id=str(project.id) if project else None,
        **kwargs,
    )
    db_session.add(thread)
    db_session.commit()
    db_session.refresh(thread)
    return thread


def test_promote_writes_cell_into_project_code(db_session, project_with_dir, tmp_path):
    thread = _thread(db_session, project_with_dir)
    result = _promote_cell_to_workspace(
        db_session,
        thread,
        {"path": "data_processing.R", "content": "library(dplyr)\nfiltered <- df %>% filter(x > 1)\n"},
        turn_id="turn-1",
    )
    assert result["status"] == "ok"
    assert result["path"] == "code/data_processing.R"
    written = (tmp_path / "code" / "data_processing.R").read_text()
    assert "filtered <- df" in written


def test_promote_requires_project_attachment(db_session):
    thread = _thread(db_session, None)
    result = _promote_cell_to_workspace(db_session, thread, {"path": "x.R", "content": "1"})
    assert result["status"] == "error"
    assert "not attached" in result["error"]


def test_promote_requires_generated_workspace(db_session, project_with_dir):
    project_with_dir.project_dir = None
    db_session.commit()
    thread = _thread(db_session, project_with_dir)
    result = _promote_cell_to_workspace(db_session, thread, {"path": "x.R", "content": "1"})
    assert result["status"] == "error"
    assert "no generated workspace" in result["error"]


def test_promote_rejects_bad_suffix_and_escaping(db_session, project_with_dir):
    thread = _thread(db_session, project_with_dir)
    bad_suffix = _promote_cell_to_workspace(db_session, thread, {"path": "x.py", "content": "1"})
    assert bad_suffix["status"] == "error"
    escape = _promote_cell_to_workspace(db_session, thread, {"path": "../outside.R", "content": "1"})
    assert escape["status"] == "error"
    assert "escapes" in escape["error"]

def test_promote_respects_locks(db_session, project_with_dir, tmp_path):
    (tmp_path / ".omicsbase").mkdir(exist_ok=True)
    (tmp_path / ".omicsbase" / "locks.json").write_text(json.dumps({"paths": ["code/data_processing.R"]}))
    thread = _thread(db_session, project_with_dir)
    result = _promote_cell_to_workspace(
        db_session,
        thread,
        {"path": "data_processing.R", "content": "x <- 1"},
    )
    assert result["status"] == "error"
    assert "locked" in result["error"]


def test_result_artifacts_include_note_tables(tmp_path):
    project = Project(
        name="p",
        question="q",
        status="completed",
        tenant_id="default_tenant",
        owner_id="default_user",
        project_dir=str(tmp_path),
    )
    results_dir = tmp_path / "output" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "alpha.csv").write_text("x\n1\n")
    note_tables = tmp_path / ".omicsbase" / "note-executions" / "exec-1" / "tables"
    note_tables.mkdir(parents=True)
    (note_tables / "table_001.csv").write_text("y\n2\n")

    paths = list_project_result_artifacts(project)
    assert "output/results/alpha.csv" in paths
    assert ".omicsbase/note-executions/exec-1/tables/table_001.csv" in paths
