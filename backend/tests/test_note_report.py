"""Regression coverage for one-way NoteThread to Quarto promotion."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.project import Project


def test_note_thread_exports_as_draft_quarto_source_without_rendering(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    setup_db = testing_session()
    project = Project(
        id=str(uuid.uuid4()),
        name="Export workspace",
        tenant_id="tenant_a",
        owner_id="user_a",
        project_dir=str(workspace_dir),
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
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"}

    try:
        thread = client.post(
            "/api/notes",
            headers=headers,
            json={"title": "Validated association exploration"},
        ).json()
        thread_id = thread["id"]
        client.post(
            f"/api/notes/{thread_id}/cells",
            headers=headers,
            json={"cell_type": "markdown", "content": "The validated question."},
        )
        client.post(
            f"/api/notes/{thread_id}/cells",
            headers=headers,
            json={"cell_type": "code", "language": "r", "content": "mean(c(1, 2, 3))"},
        )
        attached = client.post(
            f"/api/notes/{thread_id}/attach",
            headers=headers,
            json={"project_id": project_id},
        )
        assert attached.status_code == 200

        exported = client.post(
            f"/api/projects/{project_id}/reports/from-note/{thread_id}",
            headers=headers,
            json={},
        )
        assert exported.status_code == 201
        report = exported.json()
        assert report["status"] == "draft"
        assert report["rendered_path"] is None
        assert report["source_path"].startswith("code/notes/")
        source = Path(workspace_dir) / report["source_path"]
        content = source.read_text()
        assert "Validated association exploration" in content
        assert "mean(c(1, 2, 3))" in content
        assert "## Execution provenance" in content
        assert report["metadata"]["source_note_thread_id"] == thread_id

        repeated = client.post(
            f"/api/projects/{project_id}/reports/from-note/{thread_id}",
            headers=headers,
            json={},
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == report["id"]
        assert repeated.json()["metadata"]["source_sha256"] == report["metadata"]["source_sha256"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
