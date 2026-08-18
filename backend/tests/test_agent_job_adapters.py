"""Headless agent-job and pipeline-adapter tests for the one-loop architecture.

The provider stream is scripted; everything else (executor, tools, journal,
job bookkeeping, plan persistence) runs for real. Sessions are used in the
create/run/verify pattern because the task closes its own session.
"""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Job, Project
from app.services import agent_loop
from app.tasks.analysis import BUILD_INSTRUCTION, PLAN_INSTRUCTION, run_agent_job

from agent_test_helpers import openhands_from_stream, text_turn, tool_turn


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


def _patch_task(monkeypatch, factory, fake_run_native):
    monkeypatch.setattr(agent_loop, "run_native_agent", fake_run_native)
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


def test_plan_instruction_constants_are_stable():
    assert "set_plan" in PLAN_INSTRUCTION
    assert "never use, copy, or reference" in PLAN_INSTRUCTION
    assert "from scratch" in BUILD_INSTRUCTION
    assert "continue from" in BUILD_INSTRUCTION


def test_run_agent_job_plan_turn_persists_plan(monkeypatch, tmp_path):
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path)
    jid = _seed_job(factory, pid, "plan")

    plan_payload = {
        "project_name": "Job project",
        "study_type": "case-control",
        "question": "Compare the groups",
        "domain": "microbiome",
        "report_pack_id": "microbiome-diversity",
        "workflow": [
            {"id": "alpha", "name": "Alpha diversity", "classification": "standard"},
        ],
    }

    async def fake_stream(**kwargs):
        for event in tool_turn("set_plan", {"plan": plan_payload}):
            yield event

    calls = []

    async def fake_run_native(executor, message, *, cancel_check=None):
        calls.append(message)
        async for event in openhands_from_stream(fake_stream)(executor, message, cancel_check=cancel_check):
            yield event

    _patch_task(monkeypatch, factory, fake_run_native)

    result = run_agent_job(pid, jid, instruction=PLAN_INSTRUCTION, job_kind="plan")
    assert result["status"] == "completed"
    assert calls and "Plan this analysis" in calls[0]
    verify = _project_row(factory, pid)
    assert verify["analysis_plan"]["report_pack_id"] == "microbiome-diversity"


def test_run_agent_job_build_turn_renders_and_repairs(monkeypatch, tmp_path):
    """The scripted build: stage → render fails → render passes → summary."""
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path)
    jid = _seed_job(factory, pid, "generate")
    (tmp_path / "project" / "code").mkdir(parents=True)
    (tmp_path / "project" / "code" / "index.qmd").write_text("# Stub report\n")

    script = [
        tool_turn("stage_report_pack"),
        tool_turn("render_report"),
        tool_turn("render_report"),
        text_turn("The report built and rendered successfully."),
    ]

    async def fake_stream(**kwargs):
        for event in script.pop(0):
            yield event

    render_calls = []

    async def fake_render_handler(arguments):
        render_calls.append(arguments)
        if len(render_calls) == 1:
            return {
                "status": "error",
                "render_status": "failed",
                "errors": [{"step": "qmd", "file": "code/index.qmd", "error": "object 'otu' not found"}],
                "failed_page": "index.qmd",
            }
        return {"status": "completed", "render_status": "completed", "pages": ["index.html"]}

    async def fake_run_native(executor, message, *, cancel_check=None):
        executor.render_handler = fake_render_handler
        async for event in openhands_from_stream(fake_stream)(executor, message, cancel_check=cancel_check):
            yield event

    _patch_task(monkeypatch, factory, fake_run_native)

    result = run_agent_job(pid, jid, instruction=BUILD_INSTRUCTION, job_kind="generate")
    assert result["status"] == "completed"
    assert "rendered successfully" in result["final"]
    assert len(render_calls) == 2, "the model must see the first failure and re-render"


def test_run_agent_job_bridges_ask_user_to_clarifications(monkeypatch, tmp_path):
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path, status="planning")
    jid = _seed_job(factory, pid, "plan")

    pending = {
        "id": "question-1",
        "question": "Which column defines the groups?",
        "options": ["condition", "treatment"],
        "multiple": False,
    }

    async def fake_stream(**kwargs):
        for event in text_turn(pending["question"]):
            yield event

    async def fake_run_native(executor, message, *, cancel_check=None):
        async for event in openhands_from_stream(fake_stream)(executor, message, cancel_check=cancel_check):
            if event.get("type") == "final":
                event["awaiting_answer"] = pending
            yield event

    _patch_task(monkeypatch, factory, fake_run_native)

    result = run_agent_job(pid, jid, instruction=PLAN_INSTRUCTION, job_kind="plan")
    assert result["status"] == "completed"
    verify = _project_row(factory, pid)
    pending_clarifications = (verify["agent_memory"] or {}).get("pending_clarifications")
    assert pending_clarifications and pending_clarifications["questions"][0]["prompt"] == pending["question"]
    assert verify["status"] == "needs_clarification"


def test_pipeline_generate_endpoint_dispatches_agent_job(monkeypatch, tmp_path):
    """POST /generate keeps its contract but starts one loop turn."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.database import get_db

    engine, factory = _db()
    pid = _seed_project(factory, tmp_path, plan={"project_name": "p", "study_type": "cc", "question": "q"})
    db = factory()

    dispatched = []

    def fake_dispatch(task_func, project_arg, job, db_arg, background_tasks=None, *, task_kwargs=None):
        dispatched.append((getattr(task_func, "__name__", str(task_func)), dict(task_kwargs or {})))
        # The real dispatcher hands the job to Celery/background tasks; the
        # test only proves the wiring, so mark it terminal here.
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
        assert "render_report" in dispatched[0][1]["instruction"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def test_set_plan_accepts_json_string_encoded_plan(monkeypatch, tmp_path):
    """Providers that serialize nested objects as text still get a stored plan."""
    engine, factory = _db()
    pid = _seed_project(factory, tmp_path)
    jid = _seed_job(factory, pid, "plan")

    plan_payload = {
        "project_name": "String plan project",
        "study_type": "case-control",
        "question": "Compare groups",
        "domain": "microbiome",
        "report_pack_id": None,
        "workflow": [{"id": "alpha", "name": "Alpha diversity", "classification": "standard"}],
    }

    async def fake_stream(**kwargs):
        import json as _json
        from agent_test_helpers import tool_turn

        for event in tool_turn("set_plan", {"plan": _json.dumps(plan_payload)}):
            yield event

    async def fake_run_native(executor, message, *, cancel_check=None):
        from agent_test_helpers import openhands_from_stream

        async for event in openhands_from_stream(fake_stream)(executor, message, cancel_check=cancel_check):
            yield event

    _patch_task(monkeypatch, factory, fake_run_native)
    from app.tasks.analysis import PLAN_INSTRUCTION, run_agent_job

    result = run_agent_job(pid, jid, instruction=PLAN_INSTRUCTION, job_kind="plan")
    assert result["status"] == "completed"
    verify = _project_row(factory, pid)
    assert verify["analysis_plan"]["project_name"] == "String plan project"
    assert verify["analysis_plan"]["report_pack_id"] is None
