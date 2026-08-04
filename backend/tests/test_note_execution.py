"""Tests for durable isolated NoteThread cell execution contracts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

R_AVAILABLE = shutil.which("Rscript") is not None
from app.models.notes import CellExecution, NoteExecutionArtifact, NoteExecutionEvent
from app.models.project import Project


def _setup_db(project_dir="/tmp/execution-workspace"):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    setup_db = testing_session()
    project = Project(
        id=str(uuid.uuid4()),
        name="Execution workspace",
        question="Test execution",
        tenant_id="tenant_a",
        owner_id="user_a",
        project_dir=project_dir,
    )
    setup_db.add(project)
    setup_db.commit()
    project_id = str(project.id)
    setup_db.close()

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return engine, testing_session, project_id


def test_execute_queues_only_persisted_code_revision(monkeypatch):
    engine, testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    dispatched = []

    def fake_dispatch(execution_id, requested_project_id, background_tasks):
        dispatched.append((execution_id, requested_project_id))

    monkeypatch.setattr(
        "app.api.projects_note_executions._dispatch",
        fake_dispatch,
    )

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Executable note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "1 + 1"},
        ).json()

        response = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={"parameters": {"seed": 7}, "timeout_seconds": 30},
        )
        assert response.status_code == 202
        execution = response.json()
        assert execution["status"] == "queued"
        assert execution["timeout_seconds"] == 30
        assert execution["parameters"] == {"seed": 7}
        assert execution["revision_id"] == cell["revisions"][0]['id']
        assert len(dispatched) == 1

        stored = testing_session().query(CellExecution).one()
        assert stored.status == "queued"
        assert stored.input_fingerprint
        assert stored.environment_fingerprint
        assert execution["event_sequence"] == 1
        event_db = testing_session()
        events = (
            event_db.query(NoteExecutionEvent)
            .filter(NoteExecutionEvent.execution_id == stored.id)
            .order_by(NoteExecutionEvent.sequence.asc())
            .all()
        )
        assert [(item.sequence, item.event_type, item.status) for item in events] == [
            (1, "note_execution_queued", "queued"),
        ]
        event_db.close()

        replay_url = f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/executions/{execution['id']}/events"
        replay = client.get(replay_url, headers=headers)
        assert replay.status_code == 200
        assert [item["sequence"] for item in replay.json()] == [1]
        replay_tail = client.get(replay_url + "?after_sequence=1", headers=headers)
        assert replay_tail.status_code == 200
        assert replay_tail.json() == []
        forbidden_replay = client.get(
            replay_url,
            headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"},
        )
        assert forbidden_replay.status_code == 404

        forbidden = client.get(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/executions/{execution['id']}",
            headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"},
        )
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_workspace_note_queues_before_report_generation(monkeypatch, tmp_path):
    engine, _testing_session, project_id = _setup_db(project_dir=None)
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    from app.config import settings

    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))
    monkeypatch.setattr(settings, "note_execution_cache_enabled", True)
    monkeypatch.setattr(settings, "note_execution_shared_workspace", False)
    monkeypatch.setattr("app.api.projects_note_executions._dispatch", lambda *args: None)

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Pre-generation note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "1 + 1"},
        ).json()
        response = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={"cache_policy": "reuse"},
        )

        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert (tmp_path / project_id).is_dir()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_queued_execution_can_be_cancelled_idempotently(monkeypatch):
    engine, testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}

    monkeypatch.setattr("app.api.projects_note_executions._dispatch", lambda *args: None)

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Cancellation note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "Sys.sleep(10)"},
        ).json()
        execution = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={},
        ).json()

        cancel_url = (
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/"
            f"{cell['id']}/executions/{execution['id']}/cancel"
        )
        cancelled = client.post(cancel_url, headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["cancel_requested"] is True

        repeated = client.post(cancel_url, headers=headers)
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "cancelled"
        event_db = testing_session()
        events = (
            event_db.query(NoteExecutionEvent)
            .filter(NoteExecutionEvent.execution_id == execution["id"])
            .order_by(NoteExecutionEvent.sequence.asc())
            .all()
        )
        assert [(item.sequence, item.event_type, item.status) for item in events] == [
            (1, "note_execution_queued", "queued"),
            (2, "note_execution_cancelled", "cancelled"),
        ]
        event_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_non_code_cell_cannot_be_executed(monkeypatch):
    engine, _testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    monkeypatch.setattr("app.api.projects_note_executions._dispatch", lambda *args: None)

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Markdown note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "markdown", "content": "Not executable"},
        ).json()
        response = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={},
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_r_execution_uses_fixed_script_and_bounded_preview(tmp_path, monkeypatch):
    from app.services import note_execution

    captured = {}

    async def fake_run_command(cmd, cwd, timeout, cancel_check=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return True, "result\n"

    monkeypatch.setattr(note_execution, "_run_command", fake_run_command)
    from app.config import settings

    monkeypatch.setattr(settings, "note_kernel_enabled", False)
    status, metadata, error = await note_execution.execute_r_cell(
        project_dir=str(tmp_path),
        execution_id=str(uuid.uuid4()),
        source="cat('result')",
        language="r",
        parameters={"seed": 1},
        timeout_seconds=20,
    )

    assert status == "completed"
    assert error is None
    assert captured["cmd"][0:2] == ["Rscript", "--vanilla"]
    assert captured["cmd"][-1].endswith("/cell.R")
    assert captured["cwd"] == str(tmp_path)
    assert metadata["stdout_preview"] == "result\n"
    assert metadata["output_truncated"] is False
    artifact = metadata["artifacts"][0]
    assert artifact["artifact_type"] == "console"
    assert artifact["relative_path"].startswith("output/derived/note-executions/")
    artifact_path = tmp_path / artifact["relative_path"]
    assert artifact_path.read_bytes() == b"result\n"
    assert artifact["byte_size"] == len(b"result\n")
    assert artifact["sha256"] == hashlib.sha256(b"result\n").hexdigest()


def test_driver_rewrites_multiline_cells_without_vapply_length_error():
    """The print->.note_display rewrite must collapse deparse output.

    deparse() returns a character vector of length > 1 for multi-line
    expressions, so feeding it to vapply(..., character(1)) fails with
    "values must be length 1, but FUN(X[[1]]) result is length N" and
    the harness dies before the cell code ever runs.
    """
    from app.services.note_execution import _evaluate_driver

    source = (
        "# Rename non-top phyla as \"Other\"\n"
        "phylum_renamed <- lapply(x, function(v) {\n"
        "    if (v %in% top) { v } else { \"Other\" }\n"
        "})\n"
        "print(phylum_renamed)\n"
    )
    driver = _evaluate_driver(
        source=source,
        run_dir_rel=".omicsbase/note-executions/test",
        shared_workspace=False,
        quiet_package_startup=True,
        capture_plots=True,
    )
    rewrite = driver.split(".note_source <- tryCatch({", 1)[1].split(
        "}, error = function(e) e)",
        1,
    )[0]
    assert "paste(deparse(e, width.cutoff = 500L), collapse = '\\n')" in rewrite
    assert "character(1)), collapse = '\\n')" in rewrite


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not available")
def test_harness_merges_incremental_base_graphics(tmp_path):
    """Incremental additions (points/legend) merge into one figure; new
    plot() calls start new figures — knitr-compatible capture semantics."""
    from app.services.note_execution import _evaluate_driver

    run_dir = tmp_path / ".omicsbase" / "note-executions" / "smoke"
    run_dir.mkdir(parents=True)
    source = (
        "boxplot(mtcars$mpg, col = 'lightcoral', main = 'Boxplot')\n"
        "points(mean(mtcars$mpg), col = 'red', pch = 18, cex = 2)\n"
        "points(median(mtcars$mpg), col = 'blue', pch = 18, cex = 2)\n"
        "legend('topright', legend = c('Mean', 'Median'), col = c('red', 'blue'), pch = 18, bty = 'n')\n"
        "hist(rnorm(30), main = 'hist one')\n"
        "hist(rnorm(30), main = 'hist two')\n"
    )
    driver = _evaluate_driver(
        source=source,
        run_dir_rel=".omicsbase/note-executions/smoke",
        shared_workspace=False,
        quiet_package_startup=True,
        capture_plots=True,
    )
    cell = run_dir / "cell.R"
    cell.write_text(driver)
    completed = subprocess.run(
        ["Rscript", "--vanilla", str(cell)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-500:]
    plots = sorted((run_dir / "plots").glob("*.png"))
    assert len(plots) == 3, [p.name for p in plots]
    events = [
        json.loads(line)
        for line in (run_dir / ".note_events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    plot_events = [event["path"] for event in events if event["type"] == "plot"]
    assert plot_events == [str(path.relative_to(tmp_path)) for path in plots]


def test_task_persists_artifact_and_provenance(monkeypatch, tmp_path):
    engine, testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    monkeypatch.setattr("app.api.projects_note_executions._dispatch", lambda *args: None)

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Durable output note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "cat(1)"},
        ).json()
        execution = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={},
        ).json()

        setup_db = testing_session()
        project = setup_db.query(Project).filter(Project.id == project_id).one()
        project.project_dir = str(tmp_path)
        setup_db.commit()
        setup_db.close()

        relative_path = f"output/derived/note-executions/{execution['id']}/console.log"
        output_bytes = b"durable output\n"
        output_path = tmp_path / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(output_bytes)

        async def fake_execute_r_cell(**kwargs):
            assert kwargs["project_dir"] == str(tmp_path)
            return "completed", {
                "stdout_preview": "durable output\n",
                "artifacts": [{
                    "artifact_type": "console",
                    "relative_path": relative_path,
                    "mime_type": "text/plain",
                    "byte_size": len(output_bytes),
                    "sha256": hashlib.sha256(output_bytes).hexdigest(),
                }],
            }, None

        from app.tasks import notes as notes_task
        monkeypatch.setattr(notes_task, "SessionLocal", testing_session)
        monkeypatch.setattr(notes_task, "execute_r_cell", fake_execute_r_cell)
        monkeypatch.setattr(notes_task, "_publish", lambda *args: None)
        task = notes_task.run_note_cell_execution
        result = task.run(project_id, execution['id']) if hasattr(task, "run") else task(project_id, execution['id'])
        assert result["status"] == "completed"

        verify_db = testing_session()
        stored = verify_db.query(CellExecution).filter(CellExecution.id == execution['id']).one()
        artifact_row = verify_db.query(NoteExecutionArtifact).filter(NoteExecutionArtifact.execution_id == stored.id).one()
        assert stored.result_metadata["provenance"]["revision_id"] == cell["revisions"][0]['id']
        assert stored.result_metadata["artifacts"][0]['id'] == str(artifact_row.id)
        assert artifact_row.sha256 == hashlib.sha256(output_bytes).hexdigest()
        events = (
            verify_db.query(NoteExecutionEvent)
            .filter(NoteExecutionEvent.execution_id == stored.id)
            .order_by(NoteExecutionEvent.sequence.asc())
            .all()
        )
        assert [(item.sequence, item.event_type, item.status) for item in events] == [
            (1, "note_execution_queued", "queued"),
            (2, "note_execution_started", "running"),
            (3, "note_execution_completed", "completed"),
        ]
        artifact_id = str(artifact_row.id)
        verify_db.close()

        content_url = (
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/executions/"
            f"{execution['id']}/artifacts/{artifact_id}/content"
        )
        content = client.get(content_url, headers=headers)
        assert content.status_code == 200
        assert content.content == output_bytes

        forbidden = client.get(
            content_url,
            headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"},
        )
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)

