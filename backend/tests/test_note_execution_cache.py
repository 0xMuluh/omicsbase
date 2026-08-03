"""Tests for explicit NoteThread execution cache reuse."""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.notes import CellExecution, NoteExecutionArtifact, NoteExecutionEvent
from app.models.project import Project
from app.services.note_execution_cache import dependency_fingerprint, execution_cache_key


def _setup_db():
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
        name="Cache workspace",
        question="Test cache",
        tenant_id="tenant_a",
        owner_id="user_a",
        project_dir="/tmp/cache-workspace",
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


def test_opt_in_cache_reuses_checksum_validated_execution(monkeypatch, tmp_path):
    engine, testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    dispatched = []

    def fake_dispatch(execution_id, requested_project_id, background_tasks):
        dispatched.append((execution_id, requested_project_id))

    monkeypatch.setattr("app.api.projects_note_executions._dispatch", fake_dispatch)
    from app.config import settings

    # Cache reuse is only meaningful without the shared notebook workspace.
    monkeypatch.setattr(settings, "note_execution_shared_workspace", False)

    try:
        thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Cache note"},
        ).json()
        cell = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "cat(42)"},
        ).json()

        first = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={"cache_policy": "off"},
        ).json()
        assert first["cache_policy"] == "off"
        assert first["cache_hit"] is False
        assert first["cache_key"]
        assert len(dispatched) == 1

        setup_db = testing_session()
        project = setup_db.query(Project).filter(Project.id == project_id).one()
        project.project_dir = str(tmp_path)
        setup_db.commit()
        setup_db.close()

        relative_path = f"output/derived/note-executions/{first['id']}/console.log"
        output_bytes = b"cacheable output\n"
        output_path = tmp_path / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(output_bytes)

        async def fake_execute_r_cell(**kwargs):
            return "completed", {
                "stdout_preview": "cacheable output\n",
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
        result = task.run(project_id, first["id"]) if hasattr(task, "run") else task(project_id, first["id"])
        assert result["status"] == "completed"

        second_response = client.post(
            f"/api/projects/{project_id}/notes/{thread['id']}/cells/{cell['id']}/execute",
            headers=headers,
            json={"cache_policy": "reuse"},
        )
        assert second_response.status_code == 202
        second = second_response.json()
        assert second["status"] == "completed"
        assert second["execution_kind"] == "cached"
        assert second["cache_policy"] == "reuse"
        assert second["cache_hit"] is True
        assert second["cache_source_execution_id"] == first["id"]
        assert second["cache_key"] == first["cache_key"]
        assert len(dispatched) == 1

        verify_db = testing_session()
        stored = (
            verify_db.query(CellExecution)
            .filter(CellExecution.id == second["id"])
            .one()
        )
        events = (
            verify_db.query(NoteExecutionEvent)
            .filter(NoteExecutionEvent.execution_id == stored.id)
            .order_by(NoteExecutionEvent.sequence.asc())
            .all()
        )
        artifact = (
            verify_db.query(NoteExecutionArtifact)
            .filter(NoteExecutionArtifact.execution_id == stored.id)
            .one()
        )
        assert [(item.sequence, item.event_type, item.status) for item in events] == [
            (1, "note_execution_queued", "queued"),
            (2, "note_execution_cache_hit", "completed"),
        ]
        assert artifact.relative_path == relative_path
        assert stored.result_metadata["cache"]["source_execution_id"] == first["id"]
        verify_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_dependency_fingerprint_changes_when_upstream_artifact_changes():
    first = SimpleNamespace(
        id="upstream-a",
        cache_key="source-key",
        input_fingerprint="input-a",
        environment_fingerprint="env-a",
        artifacts=[
            SimpleNamespace(
                artifact_type="console",
                relative_path="output/a.log",
                sha256="a" * 64,
                byte_size=10,
            )
        ],
    )
    second = SimpleNamespace(
        id="upstream-a",
        cache_key="source-key",
        input_fingerprint="input-a",
        environment_fingerprint="env-a",
        artifacts=[
            SimpleNamespace(
                artifact_type="console",
                relative_path="output/a.log",
                sha256="b" * 64,
                byte_size=10,
            )
        ],
    )
    assert dependency_fingerprint([first]) != dependency_fingerprint([second])
    assert execution_cache_key(
        input_fingerprint="input",
        environment_fingerprint="env",
        dependency_fingerprint=dependency_fingerprint([first]),
        timeout_seconds=120,
    ) != execution_cache_key(
        input_fingerprint="input",
        environment_fingerprint="env",
        dependency_fingerprint=dependency_fingerprint([second]),
        timeout_seconds=120,
    )

