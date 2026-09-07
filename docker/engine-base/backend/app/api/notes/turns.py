"""Durable NoteThread turn streaming endpoint."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id
from app.database import get_db
from app.schemas.schemas import NoteThreadTurnRequest

note_agent_router = APIRouter(prefix="/api/notes", tags=["notes"])


def _wait_for_note_execution(*args, **kwargs):
    from app.services.note_turn.orchestrator import _wait_for_note_execution as implementation

    return implementation(*args, **kwargs)


@note_agent_router.post("/{thread_id}/turn")
async def note_thread_turn(
    thread_id: str,
    data: NoteThreadTurnRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Run one autonomous turn while persisting every visible action as a cell."""
    from app.services.note_turn.orchestrator import run_note_turn

    return await run_note_turn(
        thread_id,
        data,
        background_tasks,
        db,
        tenant_id,
        user_id,
    )
