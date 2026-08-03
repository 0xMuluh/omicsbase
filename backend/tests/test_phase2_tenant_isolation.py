"""Red/Green test suite for Phase 2: Tenant Isolation & IDOR Vulnerability Prevention."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, engine as db_engine, get_db
from app.main import app
from sqlalchemy.pool import StaticPool

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
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


def test_tenant_isolation_list_projects():
    client = TestClient(app)

    # Tenant A creates a project
    res1 = client.post(
        "/api/projects/",
        json={"name": "Tenant A Project", "question": "Q_A"},
        headers={"X-Tenant-ID": "tenant_a", "X-User-ID": "user_a"},
    )
    assert res1.status_code == 201
    proj_a_id = res1.json()["id"]

    # Tenant B lists projects
    res_b = client.get("/api/projects/", headers={"X-Tenant-ID": "tenant_b", "X-User-ID": "user_b"})
    assert res_b.status_code == 200
    b_projects = res_b.json()
    b_ids = [p["id"] for p in b_projects]
    assert proj_a_id not in b_ids


def test_tenant_isolation_idor_get_project():
    client = TestClient(app)

    # Tenant A creates a project
    res1 = client.post(
        "/api/projects/",
        json={"name": "Tenant A Secret Project"},
        headers={"X-Tenant-ID": "tenant_a"},
    )
    assert res1.status_code == 201
    proj_a_id = res1.json()["id"]

    # Tenant B tries to access Tenant A's project (IDOR)
    res_idor = client.get(f"/api/projects/{proj_a_id}", headers={"X-Tenant-ID": "tenant_b"})
    assert res_idor.status_code in (404, 403), f"IDOR Vulnerability: Tenant B accessed Tenant A project (status {res_idor.status_code})"


def test_tenant_isolation_idor_delete_project():
    client = TestClient(app)

    res1 = client.post(
        "/api/projects/",
        json={"name": "Tenant A Project to Keep"},
        headers={"X-Tenant-ID": "tenant_a"},
    )
    proj_a_id = res1.json()["id"]

    # Tenant B tries to delete Tenant A's project
    res_del = client.delete(f"/api/projects/{proj_a_id}", headers={"X-Tenant-ID": "tenant_b"})
    assert res_del.status_code in (404, 403), "IDOR Vulnerability: Tenant B deleted Tenant A project"

    # Tenant A confirms project still exists
    res_a = client.get(f"/api/projects/{proj_a_id}", headers={"X-Tenant-ID": "tenant_a"})
    assert res_a.status_code == 200


def test_tenant_isolation_idor_jobs():
    client = TestClient(app)

    # Tenant A creates a project
    res1 = client.post(
        "/api/projects/",
        json={"name": "Tenant A Project With Jobs"},
        headers={"X-Tenant-ID": "tenant_a"},
    )
    assert res1.status_code == 201
    proj_a_id = res1.json()["id"]

    # Tenant B attempts to list jobs for Tenant A's project
    res_list_b = client.get(f"/api/projects/{proj_a_id}/jobs", headers={"X-Tenant-ID": "tenant_b"})
    assert res_list_b.status_code in (404, 403), "IDOR Vulnerability: Tenant B listed Tenant A project jobs"

    # Tenant B attempts to get specific job for Tenant A's project
    res_get_b = client.get(f"/api/projects/{proj_a_id}/jobs/fake-job-id", headers={"X-Tenant-ID": "tenant_b"})
    assert res_get_b.status_code in (404, 403), "IDOR Vulnerability: Tenant B accessed Tenant A project job"

    # Tenant A can list jobs for their own project
    res_list_a = client.get(f"/api/projects/{proj_a_id}/jobs", headers={"X-Tenant-ID": "tenant_a"})
    assert res_list_a.status_code == 200


def test_tenant_isolation_idor_list_files():
    client = TestClient(app)

    # Tenant A creates a project
    res1 = client.post(
        "/api/projects/",
        json={"name": "Tenant A Project With Files"},
        headers={"X-Tenant-ID": "tenant_a"},
    )
    assert res1.status_code == 201
    proj_a_id = res1.json()["id"]

    # Tenant B attempts to list files for Tenant A's project
    res_files_b = client.get(f"/api/projects/{proj_a_id}/files", headers={"X-Tenant-ID": "tenant_b"})
    assert res_files_b.status_code in (404, 403), "IDOR Vulnerability: Tenant B listed Tenant A project files"

    # Tenant A can list files for their own project
    res_files_a = client.get(f"/api/projects/{proj_a_id}/files", headers={"X-Tenant-ID": "tenant_a"})
    assert res_files_a.status_code == 200


