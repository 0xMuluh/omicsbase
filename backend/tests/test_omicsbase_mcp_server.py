"""Tests for the OmicsBase contract-tools MCP server used by the opencode backend."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Project
from app.services import omicsbase_mcp_server
from app.services.opencode_client import opencode_mcp_config


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return factory


def _seed_project(factory, project_dir):
    db = factory()
    project = Project(
        id=str(uuid.uuid4()),
        name="MCP project",
        owner_id="user-a",
        tenant_id="tenant-a",
        question="Compare the groups",
        project_dir=str(project_dir),
    )
    db.add(project)
    db.commit()
    pid = str(project.id)
    db.close()
    return pid


def _apply_db(factory, monkeypatch):
    monkeypatch.setattr("app.database.SessionLocal", factory)


def test_opencode_mcp_config_points_at_project():
    config = json.loads(opencode_mcp_config("/tmp/some-project"))
    mcp_srv = config["mcp"]["omicsbase"]
    assert mcp_srv["type"] == "local"
    assert mcp_srv["command"] == ["python3", "-m", "app.services.omicsbase_mcp_server"]
    assert mcp_srv["environment"]["OMICSBASE_PROJECT_DIR"] == "/tmp/some-project"


def test_set_plan_commits_plan(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    pid = _seed_project(factory, tmp_path)
    monkeypatch.setenv("OMICSBASE_PROJECT_DIR", str(tmp_path))

    plan = {
        "project_name": "MCP project",
        "study_type": "case-control",
        "question": "Compare the groups",
        "domain": "microbiome",
        "workflow": [
            {"id": "alpha", "name": "Alpha diversity", "classification": "standard"},
        ],
    }
    result = omicsbase_mcp_server.set_plan(plan)
    assert result["status"] == "ok"

    db = factory()
    project = db.query(Project).filter(Project.id == pid).first()
    assert project.analysis_plan is not None
    assert project.analysis_plan["project_name"] == "MCP project"
    db.close()


def test_set_plan_rejects_invalid_plan(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    _seed_project(factory, tmp_path)
    monkeypatch.setenv("OMICSBASE_PROJECT_DIR", str(tmp_path))

    result = omicsbase_mcp_server.set_plan({"project_name": "missing fields"})
    assert result["status"] == "error"
    assert "Plan validation failed" in result["error"]


def test_set_plan_unknown_project_errors(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    monkeypatch.setenv("OMICSBASE_PROJECT_DIR", str(tmp_path))

    result = omicsbase_mcp_server.set_plan({"project_name": "nope", "domain": "microbiome"})
    assert result["status"] == "error"
    assert "No OmicsBase project found" in result["error"]


def test_ask_user_records_clarification(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    pid = _seed_project(factory, tmp_path)
    monkeypatch.setenv("OMICSBASE_PROJECT_DIR", str(tmp_path))

    result = omicsbase_mcp_server.ask_user(
        "Which grouping variable?",
        options=["diet", "meal_group"],
    )
    assert result["status"] == "ok"

    db = factory()
    project = db.query(Project).filter(Project.id == pid).first()
    pending = (project.agent_memory or {}).get("pending_clarifications")
    assert pending is not None
    assert pending["questions"][0]["prompt"] == "Which grouping variable?"
    assert pending["questions"][0]["options"] == ["diet", "meal_group"]
    db.close()

