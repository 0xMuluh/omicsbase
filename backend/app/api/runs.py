"""Tenant-scoped replay, status, and cancellation API for agent runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id
from app.database import get_db
from app.services.agent_runs import (
    get_agent_run,
    list_run_events,
    list_run_telemetry,
    request_run_cancel,
    serialize_agent_run,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _owned_run(db: Session, run_id: str, tenant_id: str):
    run = get_agent_run(db, run_id, tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.get("/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return current durable run state for reconnecting clients."""
    return serialize_agent_run(_owned_run(db, run_id, tenant_id))


@router.get("/{run_id}/events")
def get_run_events(
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Replay ordered run milestones after a client cursor."""
    _owned_run(db, run_id, tenant_id)
    return list_run_events(db, run_id, after_sequence=after_sequence, limit=limit)


@router.get("/{run_id}/telemetry")
def get_run_telemetry(
    run_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return provider/tool latency, usage, cost, and failure samples."""
    _owned_run(db, run_id, tenant_id)
    return list_run_telemetry(db, run_id, limit=limit)


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    _user_id: str = Depends(get_current_user_id),
):
    """Request cancellation; repeated requests are safe and idempotent."""
    run = _owned_run(db, run_id, tenant_id)
    request_run_cancel(db, run)
    db.commit()
    db.refresh(run)
    return serialize_agent_run(run)

