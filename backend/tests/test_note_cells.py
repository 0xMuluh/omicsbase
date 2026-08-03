"""Regression tests for the NoteThread cell compatibility metadata."""

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.models.project import ProjectMessage
from app.schemas.schemas import ProjectMessageOut
from app.services.agent_runtime import normalise_cell_type


def test_normalise_cell_type_keeps_legacy_roles_untyped():
    assert normalise_cell_type(None) is None
    assert normalise_cell_type(None, role="user") == "agent"
    assert normalise_cell_type(" AGENT ") == "agent"
    assert normalise_cell_type("output", role="tool") == "output"

    with pytest.raises(ValueError, match="Unsupported note cell type"):
        normalise_cell_type("kernel")


def test_project_message_cell_fields_are_nullable_for_legacy_rows():
    message = ProjectMessage(
        project_id=str(uuid.uuid4()),
        role="assistant",
        content="Legacy response",
    )

    assert message.cell_id is None
    assert message.cell_type is None
    assert message.cell_revision is None
    assert message.execution_id is None


def test_project_message_schema_exposes_cell_envelope():
    cell_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role="assistant",
        kind="message",
        content="Rendered result",
        message_metadata=None,
        cell_id=str(cell_id),
        cell_type="markdown",
        cell_revision=1,
        execution_id=str(execution_id),
        created_at=datetime.now(timezone.utc),
    )

    result = ProjectMessageOut.model_validate(source)

    assert result.cell_id == cell_id
    assert result.cell_type == "markdown"
    assert result.cell_revision == 1
    assert result.execution_id == execution_id

