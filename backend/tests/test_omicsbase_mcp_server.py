"""Tests for the OmicsBase MCP tools OpenCode is allowed to call."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Project
from app.services import omicsbase_mcp_server
from app.services.opencode_client import compose_workspace_prompt, opencode_mcp_config


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return factory


def _seed_project(factory, project_dir, *, status="created"):
    db = factory()
    project = Project(
        id=str(uuid.uuid4()),
        name="MCP project",
        owner_id="user-a",
        tenant_id="tenant-a",
        question="Compare the groups",
        status=status,
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


def test_mcp_exposes_ask_user_only():
    assert list(omicsbase_mcp_server._TOOLS) == ["ask_user"]
    assert not hasattr(omicsbase_mcp_server, "set_plan")


def test_compose_workspace_prompt_tells_opencode_it_owns_the_directory():
    prompt = compose_workspace_prompt("Build the report", question="Compare groups")
    assert "data/" in prompt
    assert "output/index.html" in prompt
    assert "No template dependence" in prompt
    assert "Omics-domain neutrality" in prompt
    assert "microbiome-diversity" not in prompt
    assert "finished analyses" not in prompt
    assert "Compare groups" in prompt
    assert "Build the report" in prompt
    assert "inspect_project" not in prompt
    assert "render_report" not in prompt



def test_ask_user_records_clarification(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    pid = _seed_project(factory, tmp_path, status="generating")
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
    assert project.status == "needs_clarification"
    db.close()


def test_ask_user_unknown_project_errors(tmp_path, monkeypatch):
    factory = _factory()
    _apply_db(factory, monkeypatch)
    monkeypatch.setenv("OMICSBASE_PROJECT_DIR", str(tmp_path))

    result = omicsbase_mcp_server.ask_user("Which grouping variable?")
    assert result["status"] == "error"
    assert "No OmicsBase project found" in result["error"]
