"""Project-scoped NoteThread file routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.services.note_store import get_tenant_thread as _get_note_thread_for_tenant, now as _now
from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.services.note_data import MAX_THREAD_UPLOAD_BYTES

router = APIRouter()

@router.post("/{project_id}/notes/{thread_id}/files", status_code=status.HTTP_201_CREATED)
def upload_note_thread_file(
    project_id: str,
    thread_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Attach one data file to a project NoteThread so the agent can inspect it."""
    from app.services.note_data import save_thread_upload

    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot attach files to an archived NoteThread")
    content = file.file.read(MAX_THREAD_UPLOAD_BYTES + 1)
    try:
        summary = save_thread_upload(thread, content, filename=file.filename or "upload.bin", project=project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thread.updated_at = _now()
    db.commit()
    return summary


@router.get("/{project_id}/notes/{thread_id}/files")
def list_note_thread_files(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    from app.services.note_data import list_thread_data_files

    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    return list_thread_data_files(thread, project=project)
