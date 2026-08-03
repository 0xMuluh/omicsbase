"""Transactional lifecycle events for reconnectable NoteThread executions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.notes import CellExecution, NoteExecutionEvent


def append_execution_event(
    db: Session,
    execution: CellExecution,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> NoteExecutionEvent:
    """Append one ordered event to the caller's current transaction.

    The execution row is locked before its counter is incremented. Callers
    intentionally own the commit so the state transition and its event become
    visible atomically.
    """
    if execution.id is None:
        db.flush()

    # Select only the counter while holding a row lock. Avoiding an autoflush
    # here lets the caller's status transition remain in the same transaction
    # without replacing a concurrent counter update with a stale value.
    with db.no_autoflush:
        current_sequence = (
            db.query(CellExecution.event_sequence)
            .filter(CellExecution.id == execution.id)
            .with_for_update()
            .scalar()
        )
    if current_sequence is None:
        raise ValueError(f"Note execution {execution.id} not found")

    sequence = int(current_sequence or 0) + 1
    execution.event_sequence = sequence
    event = NoteExecutionEvent(
        execution_id=execution.id,
        sequence=sequence,
        event_type=event_type,
        status=str(execution.status or "unknown"),
        event_payload=payload or {},
    )
    db.add(event)
    return event


def lock_execution(db: Session, execution: CellExecution) -> CellExecution:
    """Lock and refresh an execution before making a state transition."""
    with db.no_autoflush:
        found_id = (
            db.query(CellExecution.id)
            .filter(CellExecution.id == execution.id)
            .with_for_update()
            .scalar()
        )
    if found_id is None:
        raise ValueError(f"Note execution {execution.id} not found")
    db.refresh(execution)
    return execution
