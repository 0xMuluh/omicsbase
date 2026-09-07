"""Serialization helpers shared by the workspace API and turn services."""

from __future__ import annotations

import json


def message_payload(message) -> dict:
    metadata = dict(message.message_metadata or {}) if isinstance(message.message_metadata, dict) else None
    if metadata is not None:
        metadata.pop("reasoning", None)
    return {
        "id": str(message.id),
        "project_id": str(message.project_id),
        "role": message.role,
        "kind": message.kind,
        "content": message.content,
        "metadata": metadata,
        "cell_id": str(message.cell_id) if message.cell_id else None,
        "cell_type": message.cell_type,
        "cell_revision": message.cell_revision,
        "execution_id": str(message.execution_id) if message.execution_id else None,
        "created_at": message.created_at.isoformat(),
    }


def ndjson_event(event: dict) -> str:
    return json.dumps(event, default=str) + "\n"


__all__ = ["message_payload", "ndjson_event"]
