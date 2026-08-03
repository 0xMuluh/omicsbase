"""Integration tests for standalone Chat/Notes threads."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.project import Project


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
        name="Existing analysis workspace",
        tenant_id="tenant_a",
        owner_id="user_a",
        project_dir="/tmp/existing-analysis-workspace",
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


def test_standalone_thread_is_isolated_and_can_attach_to_workspace(monkeypatch, tmp_path):
    engine, testing_session, project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}
    monkeypatch.setattr("app.api.projects_note_executions._dispatch_standalone", lambda *args: None)

    try:
        from app.config import settings
        monkeypatch.setattr(settings, "projects_dir", str(tmp_path))

        created = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Standalone association exploration"},
        )
        assert created.status_code == 201
        thread = created.json()
        thread_id = thread["id"]
        assert thread["project_id"] is None
        assert thread["scope"] == "standalone"
        assert thread["cells"] == []

        forbidden = client.get(
            f"/api/notes/{thread_id}",
            headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"},
        )
        assert forbidden.status_code == 404

        cell_response = client.post(
            f"/api/notes/{thread_id}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "1 + 1"},
        )
        assert cell_response.status_code == 201
        cell = cell_response.json()
        assert cell["revisions"][0]["revision"] == 1

        execution_response = client.post(
            f"/api/notes/{thread_id}/cells/{cell['id']}/execute",
            headers=headers,
            json={"timeout_seconds": 30},
        )
        assert execution_response.status_code == 202
        execution = execution_response.json()
        assert execution["status"] == "queued"
        assert execution["timeout_seconds"] == 30

        events = client.get(
            f"/api/notes/{thread_id}/cells/{cell['id']}/executions/{execution['id']}/events",
            headers=headers,
        )
        assert events.status_code == 200
        assert [(item["sequence"], item["event_type"]) for item in events.json()] == [(1, "note_execution_queued")]

        attached = client.post(
            f"/api/notes/{thread_id}/attach",
            headers=headers,
            json={"project_id": project_id},
        )
        assert attached.status_code == 200
        attached_thread = attached.json()
        assert attached_thread["project_id"] == project_id
        assert attached_thread["scope"] == "workspace"
        assert attached_thread["cells"][0]["revisions"][0]["content"] == "1 + 1"

        standalone_after_attach = client.get(f"/api/notes/{thread_id}", headers=headers)
        assert standalone_after_attach.status_code == 404
        workspace_thread = client.get(
            f"/api/projects/{project_id}/notes/{thread_id}",
            headers=headers,
        )
        assert workspace_thread.status_code == 200
        assert workspace_thread.json()["scope"] == "workspace"
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)


def test_standalone_thread_can_create_a_workspace(monkeypatch, tmp_path):
    engine, _testing_session, _project_id = _setup_db()
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}

    try:
        from app.config import settings
        monkeypatch.setattr(settings, "projects_dir", str(tmp_path))
        thread = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Promote this analysis"},
        ).json()

        promoted = client.post(
            f"/api/notes/{thread['id']}/workspace",
            headers=headers,
            json={"question": "Which features associate with the phenotype?", "auto_build": False},
        )
        assert promoted.status_code == 200
        payload = promoted.json()
        assert payload["project_id"]
        assert payload["note_thread"]["project_id"] == payload["project_id"]
        assert payload["note_thread"]["scope"] == "workspace"

        workspace_thread = client.get(
            f"/api/projects/{payload['project_id']}/notes/{thread['id']}",
            headers=headers,
        )
        assert workspace_thread.status_code == 200
        assert workspace_thread.json()["title"] == "Promote this analysis"
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
