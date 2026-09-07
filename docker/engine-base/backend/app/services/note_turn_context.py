"""Context and execution-waiting helpers for notebook agent turns."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.notes import CellExecution, NoteCell, NoteThread
from app.models.project import Project
from app.services.note_payloads import execution_payload


def read_workspace_objects(thread: NoteThread) -> list[str]:
    """Return variable names currently present in the thread's shared R workspace."""
    from app.services.note_scope import thread_storage_path

    try:
        objects_path = thread_storage_path(thread) / ".omicsbase" / "note-kernel" / "workspace-objects.txt"
    except (OSError, ValueError):
        return []
    if not objects_path.is_file():
        return []
    try:
        content = objects_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = [line.strip() for line in content.splitlines() if line.strip()]
    return [name for name in names if not name.startswith(".note_")][-200:]


def build_note_agent_context(db: Session, thread: NoteThread) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    ordered_cells = sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at))
    for cell in ordered_cells[-12:]:
        revision = cell.revisions[-1] if cell.revisions else None
        if revision is None:
            continue
        executions = (
            db.query(CellExecution)
            .filter(CellExecution.revision_id == revision.id)
            .order_by(CellExecution.created_at.desc())
            .limit(2)
            .all()
        )
        execution_preview = []
        for execution in executions:
            metadata = execution.result_metadata or {}
            execution_preview.append({
                "id": str(execution.id),
                "status": execution.status,
                "stdout_preview": str(metadata.get("stdout_preview") or "")[:1800],
                "stderr_preview": str(metadata.get("stderr_preview") or "")[:1200],
                "error": str(execution.error or "")[:1200],
            })
        cells.append({
            "id": str(cell.id),
            "position": cell.position,
            "cell_type": revision.cell_type,
            "language": revision.language,
            "content": str(revision.content or "")[:6000],
            "executions": execution_preview,
        })

    context: dict[str, Any] = {
        "scope": "workspace" if thread.project_id else "standalone",
        "thread": {
            "id": str(thread.id),
            "title": thread.title,
            "thread_type": thread.thread_type,
            "status": thread.status,
        },
        "cells": cells,
        "workspace_objects": read_workspace_objects(thread),
    }
    project = None
    if thread.project_id:
        project = db.query(Project).filter(Project.id == thread.project_id).first()
    try:
        from app.services.note_data import list_thread_data_files

        context["data_files"] = list_thread_data_files(thread, project=project)
    except (OSError, ValueError):
        context["data_files"] = []
    if project is not None:
        context["workspace"] = {
            "id": str(project.id),
            "name": project.name,
            "question": project.question,
            "status": project.status,
            "agent_state": project.agent_state,
            "project_dir_available": bool(project.project_dir),
            "uploaded_files": [
                {
                    "name": item.original_name,
                    "format": item.detected_format,
                    "columns": ((item.file_summary or {}).get("columns") or [])[:30],
                }
                for item in (project.files or [])[:40]
            ],
        }
    return context


async def wait_for_note_execution(
    db: Session,
    execution_id: str,
    timeout_seconds: int,
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Wait for a queued cell execution and return its latest durable payload."""
    terminal = {"completed", "completed_with_errors", "failed", "timed_out", "cancelled"}
    if timeout_seconds is None:
        timeout_seconds = int(settings.note_execution_default_timeout_seconds)
    deadline = time.monotonic() + max(1, int(timeout_seconds)) + 60
    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            break
        execution = db.query(CellExecution).filter(CellExecution.id == execution_id).first()
        if execution is None:
            break
        if str(execution.status or "") in terminal:
            return execution_payload(execution)
        db.expire_all()
        await asyncio.sleep(1)
    execution = db.query(CellExecution).filter(CellExecution.id == execution_id).first()
    if execution is None:
        return {"id": execution_id, "status": "queued", "error": "Execution record disappeared"}
    return execution_payload(execution)

