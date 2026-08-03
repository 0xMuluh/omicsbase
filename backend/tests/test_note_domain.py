"""Tests for the separated NoteThread and Report domain."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.notes import NoteCellRevision
from app.models.project import Project


def test_note_threads_and_revisions_are_separate_from_project_chat():
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
        name="Workspace for notes",
        tenant_id="tenant_a",
        owner_id="user_a",
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
        created_thread = client.post(
            f"/api/projects/{project_id}/notes",
            headers=headers,
            json={"title": "Alpha diversity exploration"},
        )
        assert created_thread.status_code == 201
        thread = created_thread.json()
        thread_id = thread["id"]
        assert thread["project_id"] == project_id
        assert thread["cells"] == []

        created_cell = client.post(
            f"/api/projects/{project_id}/notes/{thread_id}/cells",
            headers=headers,
            json={
                "cell_type": "code",
                "language": "r",
                "content": "mean(alpha$shannon)",
            },
        )
        assert created_cell.status_code == 201
        cell = created_cell.json()
        cell_id = cell["id"]
        assert cell["revisions"][0]["revision"] == 1

        created_revision = client.post(
            f"/api/projects/{project_id}/notes/{thread_id}/cells/{cell_id}/revisions",
            headers=headers,
            json={
                "cell_type": "code",
                "language": "r",
                "content": "median(alpha$shannon)",
            },
        )
        assert created_revision.status_code == 201
        assert created_revision.json()["revision"] == 2

        detail = client.get(
            f"/api/projects/{project_id}/notes/{thread_id}",
            headers=headers,
        )
        assert detail.status_code == 200
        revisions = detail.json()["cells"][0]["revisions"]
        assert [item["revision"] for item in revisions] == [1, 2]
        assert revisions[0]["content"] == "mean(alpha$shannon)"
        assert revisions[1]["content"] == "median(alpha$shannon)"

        verify_db = testing_session()
        stored_revisions = (
            verify_db.query(NoteCellRevision)
            .order_by(NoteCellRevision.revision.asc())
            .all()
        )
        assert len(stored_revisions) == 2
        assert stored_revisions[0].content != stored_revisions[1].content
        verify_db.close()

        forbidden = client.get(
            f"/api/projects/{project_id}/notes",
            headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"},
        )
        assert forbidden.status_code == 404

        archived = client.patch(
            f"/api/projects/{project_id}/notes/{thread_id}",
            headers=headers,
            json={"status": "archived"},
        )
        assert archived.status_code == 200

        blocked = client.post(
            f"/api/projects/{project_id}/notes/{thread_id}/cells",
            headers=headers,
            json={"cell_type": "markdown", "content": "No mutation"},
        )
        assert blocked.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)

