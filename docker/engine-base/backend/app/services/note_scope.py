"""Shared scope and storage helpers for standalone and workspace NoteThreads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings


def standalone_storage_path(thread_id: str) -> Path:
    """Return the private storage root for a standalone NoteThread."""
    safe_id = str(thread_id).strip()
    if not safe_id or Path(safe_id).name != safe_id or safe_id in {".", ".."}:
        raise ValueError("Invalid NoteThread identifier")
    root = (Path(settings.projects_dir).resolve() / "notes").resolve()
    path = (root / safe_id).resolve()
    if root not in path.parents:
        raise ValueError("Standalone NoteThread storage escaped the projects root")
    return path


def workspace_note_storage_path(project_id: str) -> Path:
    """Return the reserved runtime root for Notes attached to a workspace.

    This path is also the eventual project root used by generation. Creating it
    does not create a report; it only gives isolated NoteThread executions a
    durable place to write scripts, logs, and artifacts before a Quarto
    workspace exists.
    """
    safe_id = str(project_id).strip()
    if not safe_id or Path(safe_id).name != safe_id or safe_id in {".", ".."}:
        raise ValueError("Invalid workspace identifier")
    root = Path(settings.projects_dir).resolve()
    path = (root / safe_id).resolve()
    if root not in path.parents:
        raise ValueError("Workspace NoteThread storage escaped the projects root")
    return path


def thread_storage_path(thread: Any, project: Any | None = None) -> Path:
    """Resolve and create the execution/artifact root for a NoteThread."""
    stored_path = getattr(thread, "storage_path", None)
    if stored_path:
        path = Path(str(stored_path)).resolve()
    elif project is not None and getattr(project, "project_dir", None):
        path = Path(str(project.project_dir)).resolve()
    elif getattr(thread, "project_id", None) is None:
        path = standalone_storage_path(str(thread.id))
    else:
        project_id = getattr(project, "id", None) or getattr(thread, "project_id", None)
        path = workspace_note_storage_path(str(project_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def thread_scope(thread: Any) -> str:
    return "workspace" if getattr(thread, "project_id", None) else "standalone"


def thread_event_scope(thread: Any) -> str:
    if getattr(thread, "project_id", None):
        return str(thread.project_id)
    return f"note:{thread.id}"
