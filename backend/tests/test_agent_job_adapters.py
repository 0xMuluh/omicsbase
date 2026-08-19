"""Headless OpenCode workspace-job tests.

The OpenCode stream is scripted; staging and job bookkeeping run for real.
"""

from __future__ import annotations

import uuid

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Job, Project
from app.tasks.analysis import run_agent_job, user_instruction_for


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def _seed_project(factory, tmp_path, *, plan=None, status="created"):
    db = factory()
    project = Project(
        id=str(uuid.uuid4()),
        name="Job project",
        owner_id="user-a",
        tenant_id="tenant-a",
        question="Compare the groups",
        status=status,
        auto_build=True,
        project_dir=str(tmp_path / "project"),
        analysis_plan=plan,
    )
    db.add(project)
    db.commit()
    pid = str(project.id)
    db.close()
    return pid


def _seed_job(factory, pid, job_type):
    db = factory()
    job = Job(project_id=pid, job_type=job_type, status="pending")
    db.add(job)
    db.commit()
    jid = str(job.id)
    db.close()
    return jid


def _patch_opencode(monkeypatch, factory, fake_stream):
    monkeypatch.setattr("app.services.opencode_client.stream_opencode", fake_stream)
    monkeypatch.setattr("app.tasks.analysis._get_db_session", lambda: factory())
    monkeypatch.setattr("app.tasks.analysis._update_job", lambda _db, job_id, **kw: None)


def _project_row(factory, pid):
    db = factory()
    row = db.query(Project).filter(Project.id == pid).first()
    payload = {
        "analysis_plan": row.analysis_plan,
        "agent_memory": row.agent_memory,
        "status": row.status,
    }
    db.close()
    return payload


def test_instruction_uses_user_words_and_never_names_templates():
    assert "set_plan" not in user_instruction_for(None)
    assert "render_report" not in user_instruction_for(None)
    assert "inspect_project" not in user_instruction_for(None)
    assert "example analyses" not in user_instruction_for(None)
    assert "templates" not in user_instruction_for(None)
    assert "copy" not in user_instruction_for(None)

    class _FakeProject:
        question = "Compare the groups"
        custom_plan_text = ""
        notes = ""

    assert user_instruction_for(_FakeProject) == "Compare the groups"


def test_run_agent_job_completes_without_analysis_plan(monkeypatch, tmp_path):
    engine, factory = _db()
    monkeypatch.setattr("app.tasks.analysis.settings.projects_dir", str(tmp_path / "projects"))
    pid = _seed_project(factory, tmp_path, status="generating")
    jid = _seed_job(factory, pid, "generate")

    captured = {}

    async def fake_stream(**kwargs):
        captured["instruction"] = kwargs.get("instruction")
        code_dir = Path(kwargs["project_dir"]) / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        (code_dir / "data.R").write_text("# staged study container\n")
        yield {"type": "token", "token": "Wrote the report from observed data."}
        yield {"type": "final", "message": "Wrote the report from observed data."}

    _patch_opencode(monkeypatch, factory, fake_stream)

    result = run_agent_job(pid, jid, instruction="Compare the groups", job_kind="generate")
    assert result["status"] == "completed"
    assert "Wrote the report" in result["final"]
    assert captured["instruction"] == "Compare the groups"
    verify = _project_row(factory, pid)
    assert verify["analysis_plan"] is None
    assert verify["status"] == "completed"


def test_run_agent_job_completes_when_opencode_returns_ok(monkeypatch, tmp_path):
    engine, factory = _db()
    monkeypatch.setattr("app.tasks.analysis.settings.projects_dir", str(tmp_path / "projects"))
    pid = _seed_project(factory, tmp_path, status="generating")
    jid = _seed_job(factory, pid, "generate")
    calls = {"count": 0}

    async def fake_stream(**kwargs):
        calls["count"] += 1
        yield {"type": "final", "message": "OpenCode finished.", "ok": True}

    _patch_opencode(monkeypatch, factory, fake_stream)

    result = run_agent_job(pid, jid, instruction="Compare the groups", job_kind="generate")
    assert result["status"] == "completed"
    assert calls["count"] == 1
    verify = _project_row(factory, pid)
    assert verify["status"] == "completed"


def test_run_agent_job_fails_when_opencode_errors(monkeypatch, tmp_path):
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path, status="generating")
    jid = _seed_job(factory, pid, "generate")

    async def fake_stream(**kwargs):
        yield {"type": "error", "error": "APIError 503 from https://api.orcarouter.ai/v1/chat/completions"}
        yield {
            "type": "final",
            "message": "APIError 503 from https://api.orcarouter.ai/v1/chat/completions",
            "ok": False,
        }

    _patch_opencode(monkeypatch, factory, fake_stream)

    result = run_agent_job(pid, jid, instruction="Compare the groups", job_kind="generate")
    assert result["status"] == "failed"
    verify = _project_row(factory, pid)
    assert verify["status"] == "failed"


def test_run_agent_job_bridges_ask_user_to_clarifications(monkeypatch, tmp_path):
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path, status="generating")
    jid = _seed_job(factory, pid, "generate")

    async def fake_stream(**kwargs):
        yield {
            "type": "question",
            "question": "Which column defines the groups?",
            "options": ["condition", "treatment"],
            "multiple": False,
        }
        yield {
            "type": "final",
            "message": "Need a grouping column.",
            "awaiting_answer": {
                "question": "Which column defines the groups?",
                "options": ["condition", "treatment"],
                "multiple": False,
            },
        }

    _patch_opencode(monkeypatch, factory, fake_stream)

    result = run_agent_job(pid, jid, instruction="Compare the groups", job_kind="generate")
    assert result["status"] == "completed"
    verify = _project_row(factory, pid)
    pending = (verify["agent_memory"] or {}).get("pending_clarifications")
    assert pending and pending["questions"][0]["prompt"] == "Which column defines the groups?"
    assert verify["status"] == "needs_clarification"


def test_run_agent_job_honours_cancelled_job(monkeypatch, tmp_path):
    engine, factory = _db()
    monkeypatch.setattr("app.tasks.analysis.settings.projects_dir", str(tmp_path / "projects"))
    pid = _seed_project(factory, tmp_path, status="generating")
    jid = _seed_job(factory, pid, "generate")
    db = factory()
    job = db.query(Job).filter(Job.id == jid).first()
    job.status = "cancelled"
    db.commit()
    db.close()

    async def fake_stream(**kwargs):
        if kwargs.get("cancel_check") and kwargs["cancel_check"]():
            yield {"type": "cancelled"}
            yield {"type": "final", "message": "Run cancelled.", "ok": True, "cancelled": True}
            return
        yield {"type": "final", "message": "should not run", "ok": True}

    _patch_opencode(monkeypatch, factory, fake_stream)

    result = run_agent_job(pid, jid, instruction="Compare the groups", job_kind="generate")
    assert result["status"] == "cancelled"
    verify = _project_row(factory, pid)
    assert verify["status"] == "created"


def test_merge_step_usage_accumulates_tokens():
    from app.tasks.analysis import _merge_step_usage

    totals = {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "cost": 0.0}
    _merge_step_usage(totals, {"input_tokens": 10, "output_tokens": 4}, 0.01)
    _merge_step_usage(totals, {"input_tokens": 5, "output_tokens": 2}, None)
    assert totals["input_tokens"] == 15
    assert totals["output_tokens"] == 6
    assert totals["cost"] == 0.01


def test_pipeline_generate_endpoint_dispatches_opencode_job(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.database import get_db

    engine, factory = _db()
    pid = _seed_project(factory, tmp_path)
    db = factory()

    dispatched = []

    def fake_dispatch(task_func, project_arg, job, db_arg, background_tasks=None, *, task_kwargs=None):
        dispatched.append((getattr(task_func, "__name__", str(task_func)), dict(task_kwargs or {})))
        job.status = "completed"

    monkeypatch.setattr("app.api.projects_pipeline._dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "app.api.projects_pipeline._refresh_study_manifest",
        lambda db_arg, project_arg: ([], getattr(project_arg, "study_manifest", None) or {}),
    )
    monkeypatch.setattr("app.services.study_manifest.manifest_errors", lambda *a, **k: [])
    monkeypatch.setattr("app.api.projects_pipeline._ensure_agent_provider_available", lambda project_arg: None)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{pid}/generate",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-a"},
        )
        assert response.status_code == 202
        assert dispatched and dispatched[0][0] == "run_agent_job"
        assert dispatched[0][1]["job_kind"] == "generate"
        assert "render_report" not in dispatched[0][1]["instruction"]
        assert dispatched[0][1]["instruction"] == "Compare the groups"
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
