"""Persistence and event helpers shared by all NoteThread routes.

The HTTP routers should own route declarations and validation; this module
owns the repeated NoteThread/NoteCell lookups, payload assembly, and event
publication used by both project-scoped and standalone Notes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.notes import CellExecution, NoteCell, NoteCellRevision, NoteThread
from app.models.project import Project
from app.services.note_payloads import cell_payload


def now() -> datetime:
    return datetime.now(timezone.utc)


def get_project_thread(db: Session, project_id: str, thread_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(
            selectinload(NoteThread.cells)
            .selectinload(NoteCell.revisions)
            .selectinload(NoteCellRevision.executions)
            .selectinload(CellExecution.artifacts)
        )
        .filter(NoteThread.id == thread_id, NoteThread.project_id == project_id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NoteThread not found")
    return thread


def get_tenant_thread(db: Session, thread_id: str, tenant_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(
            selectinload(NoteThread.cells)
            .selectinload(NoteCell.revisions)
            .selectinload(NoteCellRevision.executions)
            .selectinload(CellExecution.artifacts)
        )
        .filter(NoteThread.id == thread_id, NoteThread.tenant_id == tenant_id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NoteThread not found")
    return thread


def get_standalone_thread(db: Session, thread_id: str, tenant_id: str) -> NoteThread:
    thread = (
        db.query(NoteThread)
        .options(
            selectinload(NoteThread.cells)
            .selectinload(NoteCell.revisions)
            .selectinload(NoteCellRevision.executions)
            .selectinload(CellExecution.artifacts)
        )
        .filter(
            NoteThread.id == thread_id,
            NoteThread.tenant_id == tenant_id,
            NoteThread.project_id.is_(None),
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Standalone NoteThread not found")
    return thread


def get_cell(db: Session, thread_id: str, cell_id: str) -> NoteCell:
    cell = (
        db.query(NoteCell)
        .options(
            selectinload(NoteCell.revisions)
            .selectinload(NoteCellRevision.executions)
            .selectinload(CellExecution.artifacts)
        )
        .filter(NoteCell.id == cell_id, NoteCell.thread_id == thread_id)
        .first()
    )
    if not cell:
        raise HTTPException(status_code=404, detail="NoteCell not found")
    return cell


def get_execution(
    db: Session,
    project_id: str,
    thread_id: str,
    cell_id: str,
    execution_id: str,
) -> CellExecution:
    execution = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .join(NoteThread, NoteThread.id == NoteCell.thread_id)
        .filter(
            CellExecution.id == execution_id,
            NoteCell.id == cell_id,
            NoteCell.thread_id == thread_id,
            NoteThread.project_id == project_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="Cell execution not found")
    return execution


def thread_summary_payload(thread: NoteThread) -> dict:
    return {
        "id": str(thread.id),
        "project_id": str(thread.project_id) if thread.project_id else None,
        "scope": "workspace" if thread.project_id else "standalone",
        "title": thread.title,
        "title_source": thread.title_source,
        "thread_type": thread.thread_type,
        "status": thread.status,
        "metadata": thread.thread_metadata,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def thread_payload(thread: NoteThread) -> dict:
    payload = thread_summary_payload(thread)
    payload["cells"] = [cell_payload(cell) for cell in thread.cells]
    return payload


def publish_note_event(project_id: str, thread_id: str, event_type: str) -> None:
    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id,
        {"note_thread_id": thread_id, "event_type": event_type},
    )


def publish_execution_event(
    project_id: str,
    thread_id: str,
    execution: CellExecution,
    event_type: str,
) -> None:
    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id,
        {
            "note_thread_id": thread_id,
            "note_cell_id": str(execution.revision_record.cell_id),
            "note_execution_id": str(execution.id),
            "event_type": event_type,
            "execution_status": execution.status,
            "cancel_requested": bool(execution.cancel_requested),
            "execution_event_sequence": int(execution.event_sequence or 0),
        },
    )


def delete_note_thread_files(
    db: Session,
    thread: NoteThread,
    project: Project | None = None,
) -> None:
    """Remove execution artifacts owned by one NoteThread."""
    from app.services.note_execution import NOTE_OUTPUT_ROOT
    from app.services.note_scope import thread_storage_path

    try:
        base = thread_storage_path(thread, project)
    except (OSError, ValueError):
        return
    executions = (
        db.query(CellExecution)
        .join(NoteCellRevision, NoteCellRevision.id == CellExecution.revision_id)
        .join(NoteCell, NoteCell.id == NoteCellRevision.cell_id)
        .filter(NoteCell.thread_id == thread.id)
        .all()
    )
    for execution in executions:
        shutil.rmtree(base / ".omicsbase" / "note-executions" / str(execution.id), ignore_errors=True)
        shutil.rmtree(base / NOTE_OUTPUT_ROOT / str(execution.id), ignore_errors=True)


__all__ = [
    "delete_note_thread_files",
    "get_cell",
    "get_execution",
    "get_project_thread",
    "get_standalone_thread",
    "get_tenant_thread",
    "now",
    "publish_execution_event",
    "publish_note_event",
    "thread_payload",
    "thread_summary_payload",
]
