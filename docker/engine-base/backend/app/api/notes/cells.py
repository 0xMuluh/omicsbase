"""Project-scoped NoteCell and revision routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.note_payloads import cell_payload as _cell_payload, revision_payload as _revision_payload
from app.services.note_store import (
    get_cell as _get_cell,
    get_project_thread as _get_thread,
    now as _now,
    publish_note_event as _publish_note_event,
)
from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.database import get_db
from app.models.notes import NoteCell, NoteCellRevision
from app.schemas.schemas import (
    NoteCellCreate,
    NoteCellOut,
    NoteCellRevisionCreate,
    NoteCellRevisionOut,
)
from app.services.agent_runtime import normalise_cell_type

router = APIRouter()

@router.post("/{project_id}/notes/{thread_id}/cells", response_model=NoteCellOut, status_code=status.HTTP_201_CREATED)
def create_note_cell(
    project_id: str,
    thread_id: str,
    data: NoteCellCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Append a new cell with immutable revision one."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add cells to an archived NoteThread")

    position = data.position
    if position is None:
        current_max = (
            db.query(func.max(NoteCell.position))
            .filter(NoteCell.thread_id == thread_id)
            .scalar()
        )
        position = (current_max if current_max is not None else -1) + 1

    cell = NoteCell(thread_id=thread_id, position=position, status="active")
    revision = NoteCellRevision(
        revision=1,
        cell_type=normalise_cell_type(data.cell_type),
        language=data.language,
        content=data.content,
        revision_metadata=data.metadata,
        created_by=user_id,
    )
    cell.revisions.append(revision)
    thread.updated_at = _now()
    db.add(cell)
    db.commit()
    db.refresh(cell)
    _publish_note_event(project_id, thread_id, "note_cell_created")
    return _cell_payload(cell)


@router.get("/{project_id}/notes/{thread_id}/cells/{cell_id}", response_model=NoteCellOut)
def get_note_cell(
    project_id: str,
    thread_id: str,
    cell_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Load one cell identity and all of its immutable revisions."""
    get_project_for_tenant(db, project_id, tenant_id)
    _get_thread(db, project_id, thread_id)
    return _cell_payload(_get_cell(db, thread_id, cell_id))


@router.post("/{project_id}/notes/{thread_id}/cells/{cell_id}/revisions", response_model=NoteCellRevisionOut, status_code=status.HTTP_201_CREATED)
def append_note_cell_revision(
    project_id: str,
    thread_id: str,
    cell_id: str,
    data: NoteCellRevisionCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Append a new revision; existing source rows are never updated."""
    get_project_for_tenant(db, project_id, tenant_id)
    thread = _get_thread(db, project_id, thread_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an archived NoteThread")
    cell = _get_cell(db, thread_id, cell_id)
    if cell.status != "active":
        raise HTTPException(status_code=409, detail="Cannot revise an inactive NoteCell")

    for _ in range(2):
        latest = (
            db.query(NoteCellRevision)
            .filter(NoteCellRevision.cell_id == cell_id)
            .order_by(NoteCellRevision.revision.desc())
            .first()
        )
        next_revision = (latest.revision if latest else 0) + 1
        revision = NoteCellRevision(
            cell_id=cell_id,
            revision=next_revision,
            cell_type=normalise_cell_type(data.cell_type),
            language=data.language,
            content=data.content,
            revision_metadata=data.metadata,
            created_by=user_id,
        )
        cell.updated_at = _now()
        thread.updated_at = cell.updated_at
        db.add(revision)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(revision)
        _publish_note_event(project_id, thread_id, "note_cell_revision_created")
        return _revision_payload(revision)

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Concurrent cell revision detected; retry the request",
    )
