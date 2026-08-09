"""Smoke test the active workspace stream’s durable cell envelope."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.project import Project, ProjectMessage
from app.models.runs import RunTelemetry
from app.services import home_agent, workspace_agent


def test_workspace_agent_stream_persists_typed_cells(monkeypatch):
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
        name="Existing workspace",
        question="Test question",
        tenant_id="default_tenant",
        owner_id="default_user",
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

    async def fake_stream(*args, **kwargs):
        yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}
        yield {"type": "usage", "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}}
        yield {"type": "final", "message": "Grounded response"}

    async def fake_title(prompt: str) -> str:
        return "Existing workspace"

    monkeypatch.setattr(workspace_agent, "stream_workspace_agent", fake_stream)
    monkeypatch.setattr(home_agent, "generate_project_title", fake_title)
    app.dependency_overrides[get_db] = override_get_db

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/projects/{project_id}/agent/stream",
            json={"message": "Summarise the workspace"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        assert any(event["type"] == "final" for event in events)

        verify_db = testing_session()
        messages = (
            verify_db.query(ProjectMessage)
            .filter(ProjectMessage.project_id == project_id)
            .order_by(ProjectMessage.created_at.asc())
            .all()
        )
        assert [message.cell_type for message in messages] == ["agent", "markdown"]
        assert all(message.cell_revision == 1 for message in messages)
        assert len({message.execution_id for message in messages}) == 1
        assert all(message.cell_id for message in messages)
        telemetry = (
            verify_db.query(RunTelemetry)
            .filter(RunTelemetry.operation == "workspace_turn")
            .one()
        )
        assert telemetry.input_tokens == 18
        assert telemetry.output_tokens == 7
        assert telemetry.total_tokens == 25
        assert telemetry.telemetry_metadata["provider_usage"]["total_tokens"] == 25
        verify_db.close()
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
