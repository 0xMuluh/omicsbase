"""Project-name ownership is durable across create and rename operations."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.project import Project
from app.services.project_titles import claim_auto_title


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def test_create_tracks_default_and_explicit_name_ownership():
    client = TestClient(app)

    unnamed = client.post("/api/projects/", json={})
    named = client.post("/api/projects/", json={"name": "  My Study  "})

    assert unnamed.status_code == 201
    assert unnamed.json()["name"] == "New project"
    assert unnamed.json()["name_source"] == "default"
    assert named.status_code == 201
    assert named.json()["name"] == "My Study"
    assert named.json()["name_source"] == "user"


def test_patch_name_claims_user_ownership_and_updates_memory_snapshot():
    client = TestClient(app)
    created = client.post("/api/projects/", json={}).json()

    renamed = client.patch(
        f"/api/projects/{created['id']}",
        json={"name": "  Manually Named Study  "},
    )

    assert renamed.status_code == 200
    payload = renamed.json()
    assert payload["name"] == "Manually Named Study"
    assert payload["name_source"] == "user"
    assert payload["agent_memory"]["project"]["name"] == "Manually Named Study"
    assert payload["agent_memory"]["project"]["name_source"] == "user"

    persisted = client.get(f"/api/projects/{created['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["name_source"] == "user"


def test_non_name_patch_preserves_default_ownership():
    client = TestClient(app)
    created = client.post("/api/projects/", json={}).json()

    updated = client.patch(
        f"/api/projects/{created['id']}",
        json={"status": "archived"},
    )

    assert updated.status_code == 200
    assert updated.json()["name_source"] == "default"


def test_client_cannot_spoof_auto_title_ownership():
    client = TestClient(app)

    created = client.post(
        "/api/projects/",
        json={"name": "Explicit Name", "name_source": "auto"},
    )

    assert created.status_code == 201
    assert created.json()["name_source"] == "user"


def test_patch_rejects_blank_project_name_without_losing_ownership():
    client = TestClient(app)
    created = client.post("/api/projects/", json={"name": "Keep Me"}).json()

    response = client.patch(
        f"/api/projects/{created['id']}",
        json={"name": "   "},
    )

    assert response.status_code == 422
    persisted = client.get(f"/api/projects/{created['id']}").json()
    assert persisted["name"] == "Keep Me"
    assert persisted["name_source"] == "user"


def test_auto_title_claim_transitions_default_name_once():
    db = TestingSessionLocal()
    try:
        project = Project(name="New project", name_source="default")
        db.add(project)
        db.commit()
        db.refresh(project)

        applied = claim_auto_title(
            db,
            project_id=str(project.id),
            expected_name="New project",
            proposed_name="Microbiome Diversity",
        )
        db.expire_all()
        persisted = db.query(Project).filter(Project.id == project.id).one()

        assert applied == "Microbiome Diversity"
        assert persisted.name == "Microbiome Diversity"
        assert persisted.name_source == "auto"
        assert claim_auto_title(
            db,
            project_id=str(project.id),
            expected_name="New project",
            proposed_name="Competing Title",
        ) is None
    finally:
        db.close()


def test_manual_rename_wins_while_auto_title_is_in_flight():
    title_db = TestingSessionLocal()
    rename_db = TestingSessionLocal()
    try:
        project = Project(name="New project", name_source="default")
        title_db.add(project)
        title_db.commit()
        title_db.refresh(project)
        project_id = str(project.id)
        expected_name = project.name

        manual = rename_db.query(Project).filter(Project.id == project_id).one()
        manual.name = "User Chosen Name"
        manual.name_source = "user"
        rename_db.commit()

        applied = claim_auto_title(
            title_db,
            project_id=project_id,
            expected_name=expected_name,
            proposed_name="Late Generated Title",
        )
        title_db.expire_all()
        persisted = title_db.query(Project).filter(Project.id == project_id).one()

        assert applied is None
        assert persisted.name == "User Chosen Name"
        assert persisted.name_source == "user"
    finally:
        rename_db.close()
        title_db.close()
