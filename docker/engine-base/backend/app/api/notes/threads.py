"""Project-scoped NoteThread CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.services.note_store import (
    delete_note_thread_files as _delete_note_thread_files,
    get_project_thread as _get_thread,
    now as _now,
    publish_note_event as _publish_note_event,
    thread_payload as _thread_payload,
    thread_summary_payload as _thread_summary_payload,
)
from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.database import get_db
from app.models.notes import NoteThread
from app.schemas.schemas import NoteThreadCreate, NoteThreadOut, NoteThreadSummaryOut, NoteThreadUpdate

router = APIRouter()

@router.get("/{project_id}/notes", response_model=list[NoteThreadSummaryOut])
def list_note_threads(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List note threads without loading their potentially large cell contents."""
    get_project_for_tenant(db, project_id, tenant_id)
    threads = (
        db.query(NoteThread)
        .filter(NoteThread.project_id == project_id)
        .order_by(NoteThread.updated_at.desc())
        .all()
    )
    return [_thread_summary_payload(thread) for thread in threads]


@router.post("/{project_id}/notes", response_model=NoteThreadOut, status_code=status.HTTP_201_CREATED)
def create_note_thread(
    project_id: str,
    data: NoteThreadCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Create a notebook thread inside the tenant-owned workspace."""
    get_project_for_tenant(db, project_id, tenant_id)
    title = data.title.strip()
    thread = NoteThread(
        project_id=project_id,
        tenant_id=tenant_id,
        owner_id=user_id,
        title=title,
        title_source="default" if title.lower() == "untitled note" else "user",
        thread_type=data.thread_type.strip().lower(),
        status="active",
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    _publish_note_event(project_id, str(thread.id), "note_thread_created")
    return _thread_payload(thread)


@router.get("/{project_id}/notes/{thread_id}", response_model=NoteThreadOut)
def get_note_thread(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Load one note thread and all immutable cell revisions."""
    get_project_for_tenant(db, project_id, tenant_id)
    return _thread_payload(_get_thread(db, project_id, thread_id))


@router.patch("/{project_id}/notes/{thread_id}", response_model=NoteThreadSummaryOut)
def update_note_thread(
    project_id: str,
    thread_id: str,
    data: NoteThreadUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Update thread presentation state; cell revisions remain append-only."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes:
        thread.title = changes["title"].strip()
        thread.title_source = "user"
    if "status" in changes:
        thread.status = changes["status"]
    if "metadata" in changes:
        thread.thread_metadata = changes["metadata"]
    db.commit()
    db.refresh(thread)
    _publish_note_event(project_id, thread_id, "note_thread_updated")
    return _thread_summary_payload(thread)


@router.delete("/{project_id}/notes/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note_thread(
    project_id: str,
    thread_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Permanently delete a workspace NoteThread, its executions, and files."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    _delete_note_thread_files(db, thread, project)
    db.delete(thread)
    db.commit()
    _publish_note_event(project_id, thread_id, "note_thread_deleted")
