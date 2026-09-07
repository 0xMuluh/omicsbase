"""Serialization helpers for the notebook HTTP and agent surfaces."""

from __future__ import annotations

from typing import Any

from app.models.notes import (
    CellExecution,
    NoteCell,
    NoteCellRevision,
    NoteExecutionArtifact,
    NoteExecutionEvent,
)


def revision_payload(revision: NoteCellRevision) -> dict[str, Any]:
    return {
        "id": str(revision.id),
        "cell_id": str(revision.cell_id),
        "revision": revision.revision,
        "cell_type": revision.cell_type,
        "language": revision.language,
        "content": revision.content,
        "metadata": revision.revision_metadata,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


def artifact_payload(artifact: NoteExecutionArtifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "execution_id": str(artifact.execution_id),
        "artifact_type": artifact.artifact_type,
        "relative_path": artifact.relative_path,
        "mime_type": artifact.mime_type,
        "byte_size": artifact.byte_size,
        "sha256": artifact.sha256,
        "metadata": artifact.artifact_metadata,
        "created_at": artifact.created_at,
    }


def execution_payload(execution: CellExecution) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "revision_id": str(execution.revision_id),
        "attempt": execution.attempt,
        "status": execution.status,
        "execution_kind": execution.execution_kind,
        "timeout_seconds": execution.timeout_seconds,
        "cancel_requested": bool(execution.cancel_requested),
        "environment_fingerprint": execution.environment_fingerprint,
        "input_fingerprint": execution.input_fingerprint,
        "parameters": execution.parameters,
        "result_metadata": execution.result_metadata,
        "artifacts": [artifact_payload(item) for item in execution.artifacts],
        "error": execution.error,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "created_at": execution.created_at,
        "event_sequence": int(execution.event_sequence or 0),
        "cache_policy": execution.cache_policy,
        "cache_key": execution.cache_key,
        "dependency_fingerprint": execution.dependency_fingerprint,
        "upstream_execution_ids": execution.upstream_execution_ids or [],
        "idempotency_key": execution.idempotency_key,
        "cache_hit": bool(execution.cache_hit),
        "cache_source_execution_id": execution.cache_source_execution_id,
    }


def cell_payload(cell: NoteCell) -> dict[str, Any]:
    latest_revision = cell.revisions[-1] if cell.revisions else None
    executions = list(getattr(latest_revision, "executions", []) or []) if latest_revision else []
    latest_execution = max(executions, key=lambda item: item.created_at, default=None)
    return {
        "id": str(cell.id),
        "thread_id": str(cell.thread_id),
        "position": cell.position,
        "status": cell.status,
        "revisions": [revision_payload(item) for item in cell.revisions],
        "latest_execution": execution_payload(latest_execution) if latest_execution else None,
        "created_at": cell.created_at,
        "updated_at": cell.updated_at,
    }


def note_execution_observation_payload(
    execution: dict[str, Any],
    cell: dict[str, Any],
    *,
    turn_id: str,
) -> dict[str, Any]:
    """Shape an execution result without treating queued work as success."""
    status = str((execution or {}).get("status") or "unknown").lower()
    metadata = (execution or {}).get("result_metadata") or {}
    if status == "completed":
        observation_status = "ok"
    elif status in {"queued", "running", "cancel_requested"}:
        observation_status = "pending"
    else:
        observation_status = "error"
    stderr = str(metadata.get("stderr_preview") or (execution or {}).get("error") or "")[:4000]
    return {
        "status": observation_status,
        "stdout": str(metadata.get("stdout_preview") or "")[:4000],
        "stderr": stderr,
        "summary": {
            "execution_status": status,
            "output_chars": metadata.get("output_chars", 0),
            "output_truncated": bool(metadata.get("output_truncated")),
            "had_errors": bool(metadata.get("had_errors")) or status in {"failed", "timed_out", "cancelled", "completed_with_errors"},
        },
        "cell": cell,
        "execution": execution,
        "turn_id": turn_id,
    }


def event_payload(event: NoteExecutionEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "execution_id": str(event.execution_id),
        "sequence": event.sequence,
        "event_type": event.event_type,
        "status": event.status,
        "payload": event.event_payload or {},
        "created_at": event.created_at,
    }

