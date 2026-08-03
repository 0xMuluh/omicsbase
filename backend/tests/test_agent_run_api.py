"""API security and cancellation coverage for shared agent runs."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.agent_runs import append_run_event, create_or_get_agent_run


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
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _create_run(tenant_id: str = "tenant-a"):
    db = TestingSessionLocal()
    run, _ = create_or_get_agent_run(
        db,
        tenant_id=tenant_id,
        owner_id=f"{tenant_id}-user",
        surface="notes",
        idempotency_scope=f"notes:{tenant_id}:turn",
        idempotency_key="turn-1",
        request_payload={"message": "safe replay"},
    )
    append_run_event(
        db,
        run,
        "tool_completed",
        {"secret": "Bearer secret-token-xyz123", "status": "ok"},
        idempotency_key="tool-1",
    )
    db.commit()
    run_id = str(run.id)
    db.close()
    return run_id


def test_run_replay_and_cancel_are_tenant_scoped():
    run_id = _create_run("tenant-a")
    client = TestClient(app)

    own = client.get(f"/api/runs/{run_id}", headers={"X-Tenant-ID": "tenant-a"})
    assert own.status_code == 200
    assert own.json()["status"] == "queued"

    events = client.get(
        f"/api/runs/{run_id}/events?after_sequence=0",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert events.status_code == 200
    assert [item["sequence"] for item in events.json()] == [1, 2]
    assert "secret-token-xyz123" not in json.dumps(events.json())

    forbidden = client.get(f"/api/runs/{run_id}", headers={"X-Tenant-ID": "tenant-b"})
    assert forbidden.status_code == 404

    cancelled = client.post(
        f"/api/runs/{run_id}/cancel",
        headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "tenant-a-user"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    repeated = client.post(
        f"/api/runs/{run_id}/cancel",
        headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "tenant-a-user"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"

