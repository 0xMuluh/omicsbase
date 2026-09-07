"""Tenant-scoped replay, status, and cancellation API for agent runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id
from app.database import get_db
from app.models.runs import AgentRun
from app.schemas.schemas import AgentApprovalSubmit
from app.services.agent_runs import (
    cancel_agent_run,
    cancel_registered_run_task,
    get_agent_run,
    list_run_events,
    list_run_telemetry,
    serialize_agent_run,
    sync_compatibility_jobs,
    sync_project_from_agent_run,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _owned_run(db: Session, run_id: str, tenant_id: str):
    run = get_agent_run(db, run_id, tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.get("")
def list_runs(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List recent durable runs for a project without exposing another tenant."""
    query = db.query(AgentRun).filter(AgentRun.tenant_id == str(tenant_id))
    if project_id:
        query = query.filter(AgentRun.project_id == str(project_id))
    runs = (
        query
        .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [serialize_agent_run(run) for run in runs]

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

@router.post("/{run_id}/approval")
def respond_to_approval(
    run_id: str,
    data: AgentApprovalSubmit,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    _user_id: str = Depends(get_current_user_id),
):
    """Answer one pending native coding-agent approval request."""
    run = _owned_run(db, run_id, tenant_id)
    metadata = dict(run.run_metadata or {})
    pending = metadata.get("codex_pending_approval")
    if not isinstance(pending, dict):
        raise HTTPException(status_code=409, detail="This run is not waiting for approval")
    if str(pending.get("request_id") or "") != data.request_id:
        raise HTTPException(status_code=409, detail="The approval request is no longer current")
    if run.status not in {"running", "waiting_tool"}:
        raise HTTPException(status_code=409, detail="The run can no longer accept this approval")

    approval_kind = str(pending.get("approval_kind") or "")
    if approval_kind == "item/permissions/requestApproval" and data.decision == "cancel":
        raise HTTPException(
            status_code=422,
            detail="Use Stop to cancel the run; this permission request accepts allow or decline",
        )
    details = pending.get("details") if isinstance(pending.get("details"), dict) else {}
    available = {str(value) for value in details.get("availableDecisions") or []}
    if available and data.decision not in available:
        raise HTTPException(
            status_code=422,
            detail=f"Decision {data.decision!r} is not available for this request",
        )

    metadata["codex_approval_response"] = {
        "request_id": data.request_id,
        "decision": data.decision,
    }
    run.run_metadata = metadata
    db.commit()

    from app.services.job_events import publish_project_event

    publish_project_event(
        str(run.project_id or run.id),
        {"run_id": str(run.id), "approval_response": True},
    )
    db.refresh(run)
    return serialize_agent_run(run)


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    _user_id: str = Depends(get_current_user_id),
):
    """Cancel an AgentRun and best-effort abort its provider operation."""
    run = _owned_run(db, run_id, tenant_id)
    from app.services.agent_runs import TERMINAL_RUN_STATUSES

    if run.status in TERMINAL_RUN_STATUSES:
        sync_compatibility_jobs(db, run)
        db.commit()
        return serialize_agent_run(run)

    project = None
    project_id = str(run.project_id or "").strip()
    if project_id:
        from app.models.project import Project

        project = db.query(Project).filter(Project.id == project_id).one_or_none()

    run = cancel_agent_run(db, run, reason="user stopped the workspace run")
    sync_compatibility_jobs(db, run)
    db.commit()

    from app.services.job_events import publish_project_event

    publish_project_event(
        project_id or str(run.id),
        {"run_id": str(run.id), "status": str(run.status)},
    )

    # Durable cancellation is already committed. Provider/network cleanup is
    # deliberately best effort and cannot leave the run stuck in Stopping.
    from app.services.opencode.cancellation import abort_agent_operation

    abort_agent_operation(run, project)
    cancel_registered_run_task(str(run.id))
    if project is not None:
        sync_project_from_agent_run(
            db,
            run,
            project,
            force=project.agent_state in {"generating", "rendering", "editing"},
        )
        publish_project_event(project_id, {"run_id": str(run.id), "status": "cancelled"})

    db.refresh(run)
    return serialize_agent_run(run)

