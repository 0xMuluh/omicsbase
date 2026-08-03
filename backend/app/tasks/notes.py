"""Background/Celery task for isolated NoteThread cell execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.notes import CellExecution, NoteCell, NoteCellRevision, NoteExecutionArtifact, NoteThread
from app.models.project import Project
from app.services.note_execution import execute_r_cell
from app.services.note_execution_events import append_execution_event, lock_execution
from app.services.note_scope import thread_storage_path
from app.tasks.analysis import task_decorator

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed", "timed_out", "cancelled"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_task_args(args) -> tuple[str, str, str]:
    if len(args) >= 4:
        return str(args[1]), str(args[2]), str(args[3])
    if len(args) == 3:
        if isinstance(args[0], str):
            return str(args[0]), str(args[1]), str(args[2])
        return str(args[1]), str(args[2]), "project"
    if len(args) == 2:
        return str(args[0]), str(args[1]), "project"
    raise ValueError(f"Expected scope_id and execution_id, got {args!r}")


def _publish(execution: CellExecution, project_id: str | None, thread_id: str) -> None:
    if not project_id:
        return
    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id,
        {
            "note_thread_id": thread_id,
            "note_cell_id": str(execution.revision_record.cell_id),
            "note_execution_id": str(execution.id),
            "event_type": "note_execution",
            "execution_status": execution.status,
            "cancel_requested": bool(execution.cancel_requested),
            "execution_event_sequence": int(execution.event_sequence or 0),
        },
    )


def _cancel_requested(execution_id: str) -> bool:
    db = SessionLocal()
    try:
        value = (
            db.query(CellExecution.cancel_requested)
            .filter(CellExecution.id == execution_id)
            .scalar()
        )
        return bool(value)
    finally:
        db.close()


def _load_context(db, scope_id: str, execution_id: str, scope_type: str):
    execution = db.query(CellExecution).filter(CellExecution.id == execution_id).first()
    if not execution:
        raise ValueError("Note execution not found")
    revision = db.query(NoteCellRevision).filter(NoteCellRevision.id == execution.revision_id).first()
    if not revision:
        raise ValueError("Note execution revision not found")
    cell = db.query(NoteCell).filter(NoteCell.id == revision.cell_id).first()
    thread = db.query(NoteThread).filter(NoteThread.id == cell.thread_id).first() if cell else None
    project = None
    if scope_type == "thread":
        valid_scope = bool(thread and thread.project_id is None and str(thread.id) == str(scope_id))
    else:
        project = db.query(Project).filter(Project.id == scope_id).first()
        valid_scope = bool(thread and project and str(thread.project_id) == str(project.id))
    if not cell or not thread or not valid_scope:
        raise ValueError("Note execution does not belong to the requested scope")
    return execution, revision, cell, thread, project


def _persist_artifacts(
    db,
    execution: CellExecution,
    revision: NoteCellRevision,
    metadata: dict,
) -> dict:
    """Persist output descriptors and attach immutable execution provenance."""
    result = dict(metadata or {})
    provenance = {
        "execution_id": str(execution.id),
        "revision_id": str(revision.id),
        "cell_id": str(revision.cell_id),
        "attempt": execution.attempt,
        "input_fingerprint": execution.input_fingerprint,
        "environment_fingerprint": execution.environment_fingerprint,
        "cache_key": execution.cache_key,
        "dependency_fingerprint": execution.dependency_fingerprint,
        "upstream_execution_ids": execution.upstream_execution_ids or [],
    }
    result["provenance"] = provenance
    descriptors = result.get("artifacts") or []
    persisted = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise ValueError("Invalid note execution artifact descriptor")
        artifact_type = str(descriptor.get("artifact_type") or "").strip()
        relative_path = str(descriptor.get("relative_path") or "").strip()
        if (
            not artifact_type
            or not relative_path
            or relative_path.startswith("/")
            or any(part == ".." for part in relative_path.split("/"))
        ):
            raise ValueError("Invalid note execution artifact path")
        byte_size = int(descriptor.get("byte_size") or 0)
        sha256 = str(descriptor.get("sha256") or "").strip().lower()
        if byte_size < 0 or len(sha256) != 64:
            raise ValueError("Invalid note execution artifact metadata")
        artifact = (
            db.query(NoteExecutionArtifact)
            .filter(
                NoteExecutionArtifact.execution_id == execution.id,
                NoteExecutionArtifact.artifact_type == artifact_type,
                NoteExecutionArtifact.relative_path == relative_path,
            )
            .first()
        )
        if artifact is None:
            artifact = NoteExecutionArtifact(
                execution_id=execution.id,
                artifact_type=artifact_type,
                relative_path=relative_path,
            )
            db.add(artifact)
        artifact.mime_type = str(descriptor.get("mime_type") or "application/octet-stream")
        artifact.byte_size = byte_size
        artifact.sha256 = sha256
        descriptor_metadata = descriptor.get("metadata")
        artifact.artifact_metadata = {
            **(descriptor_metadata if isinstance(descriptor_metadata, dict) else {}),
            "provenance": provenance,
        }
        db.flush()
        persisted.append({
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "relative_path": artifact.relative_path,
            "mime_type": artifact.mime_type,
            "byte_size": artifact.byte_size,
            "sha256": artifact.sha256,
            "metadata": artifact.artifact_metadata,
        })
    result["artifacts"] = persisted
    return result


def _mark_cancelled(db, execution: CellExecution, project_id: str, thread_id: str) -> None:
    execution.status = "cancelled"
    execution.finished_at = _now()
    execution.error = None
    append_execution_event(db, execution, "note_execution_cancelled")
    db.commit()
    _publish(execution, project_id, thread_id)


@task_decorator
def run_note_cell_execution(*args):
    """Execute a queued immutable R revision in the configured sandbox."""
    scope_id, execution_id, scope_type = _parse_task_args(args)
    project_id = scope_id if scope_type == "project" else None
    db = SessionLocal()
    execution = None
    thread = None
    try:
        execution, revision, _cell, thread, project = _load_context(db, scope_id, execution_id, scope_type)
        lock_execution(db, execution)
        if execution.status in TERMINAL_STATUSES:
            return {"status": execution.status, "execution_id": execution_id}

        if execution.cancel_requested or execution.status == "cancel_requested":
            _mark_cancelled(db, execution, project_id, str(thread.id))
            return {"status": "cancelled", "execution_id": execution_id}

        execution_root = thread_storage_path(thread, project)

        execution.status = "running"
        execution.started_at = _now()
        append_execution_event(db, execution, "note_execution_started")
        db.commit()
        _publish(execution, project_id, str(thread.id))

        loop = asyncio.new_event_loop()
        try:
            status, metadata, error = loop.run_until_complete(
                execute_r_cell(
                    project_dir=str(execution_root),
                    execution_id=execution_id,
                    source=revision.content,
                    language=revision.language,
                    parameters=execution.parameters,
                    timeout_seconds=execution.timeout_seconds,
                    cancel_check=lambda: _cancel_requested(execution_id),
                )
            )
        finally:
            loop.close()

        lock_execution(db, execution)
        if status == "completed":
            execution.status = "completed"
        elif status == "cancelled" or execution.cancel_requested:
            execution.status = "cancelled"
        else:
            execution.status = status
        metadata = _persist_artifacts(db, execution, revision, metadata)
        execution.result_metadata = metadata
        execution.error = error
        execution.finished_at = _now()
        terminal_payload = {
            "error": execution.error[:2_000] if execution.error else None,
            "artifact_ids": [str(item.id) for item in execution.artifacts],
        }
        append_execution_event(
            db,
            execution,
            f"note_execution_{execution.status}",
            terminal_payload,
        )
        db.commit()
        _publish(execution, project_id, str(thread.id))
        return {"status": execution.status, "execution_id": execution_id}
    except Exception as exc:
        logger.exception("Note execution %s failed", execution_id)
        if execution is not None:
            try:
                db.rollback()
            except Exception:
                pass
            execution.status = "failed"
            execution.error = str(exc)[:20_000]
            execution.finished_at = _now()
            try:
                append_execution_event(
                    db,
                    execution,
                    "note_execution_failed",
                    {"error": execution.error[:2_000]},
                )
                db.commit()
            except Exception:
                db.rollback()
            if thread is not None:
                try:
                    _publish(execution, project_id, str(thread.id))
                except Exception:
                    logger.exception("Failed to publish note execution failure event")
        return {"status": "failed", "execution_id": execution_id, "error": str(exc)}
    finally:
        db.close()

