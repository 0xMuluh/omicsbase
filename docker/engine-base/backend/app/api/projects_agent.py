"""Workspace agent execution, streaming, assistant, and job tracking endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project, ProjectMessage
from app.models.runs import AgentRun
from app.schemas.schemas import (
    JobOut,
    ProjectMessageOut,
    WorkspaceAgentRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()




@router.get("/{project_id}/messages", response_model=list[ProjectMessageOut])
def list_project_messages(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return the durable workspace conversation and tool events."""
    get_project_for_tenant(db, project_id, tenant_id)
    messages = (
        db.query(ProjectMessage)
        .filter(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at.asc())
        .all()
    )
    payloads = []
    for message in messages:
        payload = _message_payload(message)
        # ProjectMessageOut reads the ORM field name on response validation;
        # the streamed NDJSON contract uses the serialized ``metadata`` key.
        payload["message_metadata"] = payload.pop("metadata", None)
        payloads.append(payload)
    return payloads


@router.get("/{project_id}/events")
async def stream_project_events(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Push project, job, and conversation changes to the workspace."""
    get_project_for_tenant(db, project_id, tenant_id)

    from app.services.job_events import subscribe_project_events

    async def event_stream():
        previous_signature = None

        async def emit_snapshot() -> str | None:
            nonlocal previous_signature
            db.expire_all()
            snapshot = _workspace_event_snapshot(db, project_id)
            if snapshot is None:
                db.rollback()
                return None
            signature = json.dumps(snapshot, sort_keys=True, default=str)
            if signature != previous_signature:
                previous_signature = signature
                payload = _sse_event("workspace", snapshot)
            else:
                payload = ""
            # The event stream is long-lived; never carry its read transaction
            # into the next await or keepalive interval.
            db.rollback()
            return payload

        initial = await emit_snapshot()
        if initial is None:
            yield _sse_event("deleted", {"project_id": project_id})
            return
        if initial:
            yield initial

        subscriber = subscribe_project_events(project_id)
        try:
            while not await request.is_disconnected():
                try:
                    notification = await asyncio.wait_for(subscriber.__anext__(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Agent-run usage and heartbeats can change without a
                    # compatibility-job notification. Re-emit the snapshot
                    # on the keepalive so the workspace can show live timing
                    # and token counters during long provider/tool waits.
                    payload = await emit_snapshot()
                    if payload:
                        yield payload
                    else:
                        yield _sse_event("heartbeat", {"project_id": project_id})
                    continue
                except StopAsyncIteration:
                    break

                payload = await emit_snapshot()
                if payload is None:
                    yield _sse_event("deleted", {"project_id": project_id})
                    return
                if payload:
                    yield payload
                elif notification:
                    yield _sse_event("notify", notification)
        finally:
            await subscriber.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{project_id}/agent/stream")
async def workspace_agent_stream(
    project_id: str,
    data: WorkspaceAgentRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Run the unified workspace agent and stream observations as NDJSON."""
    from app.services.workspace_turn.orchestrator import run_workspace_turn

    return await run_workspace_turn(
        project_id,
        data,
        db,
        tenant_id,
        user_id,
    )

@router.post("/{project_id}/agent/cancel")
def cancel_project_agent(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    _user_id: str = Depends(get_current_user_id),
):
    """Cancel the current project run when the client has not received its id yet."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    run = (
        db.query(AgentRun)
        .filter(
            AgentRun.project_id == str(project.id),
            AgentRun.tenant_id == str(tenant_id),
            AgentRun.status.in_({"queued", "running", "waiting_tool", "cancel_requested", "paused"}),
        )
        .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
        .first()
    )
    if run is None:
        return {"project_id": str(project.id), "run_id": None, "status": "idle"}

    from app.services.agent_runs import cancel_agent_run, sync_compatibility_jobs, sync_project_from_agent_run

    run = cancel_agent_run(db, run, reason="user stopped the workspace run")
    sync_compatibility_jobs(db, run)
    sync_project_from_agent_run(db, run, project, force=True)
    db.commit()

    from app.services.job_events import publish_project_event
    from app.services.opencode.cancellation import abort_agent_operation
    from app.services.agent_runs import cancel_registered_run_task, serialize_agent_run

    publish_project_event(str(project.id), {"run_id": str(run.id), "status": str(run.status)})
    abort_agent_operation(run, project)
    cancel_registered_run_task(str(run.id))
    db.refresh(run)
    return serialize_agent_run(run)


@router.get("/{project_id}/transcript")
def export_project_transcript(
    project_id: str,
    format: str = "markdown",
    include_tools: bool = True,
    include_timestamps: bool = True,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Download the durable chat transcript as markdown or HTML."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    messages = (
        db.query(ProjectMessage)
        .filter(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at.asc())
        .all()
    )
    fmt = (format or "markdown").strip().lower()
    if fmt not in {"markdown", "md", "html"}:
        raise HTTPException(status_code=422, detail="format must be markdown or html")
    body = _render_transcript(
        project.name,
        messages,
        as_html=fmt == "html",
        include_tools=include_tools,
        include_timestamps=include_timestamps,
    )
    if fmt == "html":
        return Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="omicsbase-{project_id}-transcript.html"'
            },
        )
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="omicsbase-{project_id}-transcript.md"'
        },
    )


@router.get("/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """List all jobs for a project."""
    get_project_for_tenant(db, project_id, tenant_id)
    return db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()


@router.get("/{project_id}/jobs/{job_id}", response_model=JobOut)
def get_job(
    project_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Get a specific job."""
    get_project_for_tenant(db, project_id, tenant_id)
    job = db.query(Job).filter(Job.id == job_id, Job.project_id == project_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{project_id}/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    project_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Cancel a workspace operation through the authoritative AgentRun."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    job = db.query(Job).filter(Job.id == job_id, Job.project_id == project_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in {"completed", "failed", "cancelled", "paused"}:
        if job.agent_run_id:
            from app.services.agent_runs import get_agent_run, sync_compatibility_jobs, sync_project_from_agent_run

            terminal_run = get_agent_run(db, str(job.agent_run_id), tenant_id)
            if terminal_run is not None:
                sync_compatibility_jobs(db, terminal_run)
                sync_project_from_agent_run(db, terminal_run, project)
                db.refresh(job)
        return job

    from app.services.agent_runs import (
        TERMINAL_RUN_STATUSES,
        cancel_agent_run,
        get_agent_run,
        sync_compatibility_jobs,
        sync_project_from_agent_run,
    )

    run_id = str(job.agent_run_id or "").strip() or None
    run = get_agent_run(db, run_id, tenant_id) if run_id else None
    was_busy = project.status in {"generating", "rendering", "editing"}

    if run is not None:
        # If the worker won the race, preserve its terminal result and repair
        # only the compatibility projection instead of cancelling success.
        if run.status in TERMINAL_RUN_STATUSES:
            sync_compatibility_jobs(db, run)
            sync_project_from_agent_run(db, run, project)
            db.refresh(job)
            return job
        run = cancel_agent_run(db, run, reason="user stopped the workspace job")
        sync_compatibility_jobs(db, run)
    else:
        # Keep jobs created before AgentRun linking cancellable as a legacy
        # fallback; there is no durable run to project in this case.
        now = datetime.now(timezone.utc)
        job.status = "cancelled"
        job.error = None
        job.finished_at = now
        job.heartbeat_at = now
        job.updated_at = now

    db.commit()
    from app.services.job_events import publish_project_event

    publish_project_event(
        str(project.id),
        {
            "job_id": str(job.id),
            "agent_run_id": run_id,
            "status": str(job.status),
        },
    )

    # Provider cleanup is deliberately best effort and happens after durable
    # cancellation is committed, so the UI cannot be left waiting on it.
    if run is not None:
        from app.services.opencode.cancellation import abort_agent_operation

        abort_agent_operation(run, project)
        from app.services.agent_runs import cancel_registered_run_task

        cancel_registered_run_task(str(run.id))

    if run is not None:
        sync_project_from_agent_run(db, run, project, force=True)
    elif was_busy and str(job.status) == "cancelled":
        # Jobs predating AgentRun linking retain a small compatibility fallback.
        db.expire_all()
        project = db.query(Project).filter(Project.id == str(project_id)).one()
        project_dir = Path(project.project_dir or (Path(settings.projects_dir) / str(project.id)))
        has_workspace = (project_dir / "code").is_dir() or (project_dir / "output").is_dir()
        project.status = "completed" if has_workspace else "created"
        from app.services.agent_runtime import set_agent_state

        set_agent_state(db, project, "idle", "Run cancelled")
    publish_project_event(
        str(project.id),
        {
            "job_id": str(job.id),
            "agent_run_id": run_id,
            "status": "cancelled",
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        },
    )
    db.refresh(job)
    return job


def _workspace_event_snapshot(db: Session, project_id: str) -> dict | None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None
    jobs = (
        db.query(Job)
        .filter(Job.project_id == project_id)
        .order_by(Job.updated_at.desc())
        .limit(10)
        .all()
    )
    latest_message = (
        db.query(ProjectMessage)
        .filter(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at.desc())
        .first()
    )
    return {
        "project_id": project_id,
        "status": project.status,
        "agent_state": project.agent_state,
        "agent_summary": (project.agent_memory or {}).get("summary"),
        "pending_guidance": (project.agent_memory or {}).get("pending_guidance") or [],
        "project_updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "latest_message_id": str(latest_message.id) if latest_message else None,
        "latest_message_at": latest_message.created_at.isoformat() if latest_message else None,
        "jobs": [
            {
                "id": str(job.id),
                "agent_run_id": str(job.agent_run_id) if job.agent_run_id else None,
                "type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "logs": (job.logs or "")[-12_000:] if job.logs else None,
                "error": job.error,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
            }
            for job in jobs
        ],
        "run": _workspace_run_snapshot(db, project_id),
    }


def _workspace_run_snapshot(db: Session, project_id: str) -> dict | None:
    """Return safe live-run telemetry for the workspace status surface."""
    run = (
        db.query(AgentRun)
        .filter(AgentRun.project_id == str(project_id))
        .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
        .first()
    )
    if run is None:
        return None
    metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    usage = metadata.get("usage_totals") if isinstance(metadata.get("usage_totals"), dict) else {}
    measurements = metadata.get("measurements") if isinstance(metadata.get("measurements"), dict) else {}
    return {
        "id": str(run.id),
        "status": run.status,
        "kind": run.kind,
        "current_step": run.current_step,
        "event_sequence": run.event_sequence,
        "cancel_requested": bool(run.cancel_requested),
        "resumable": bool(run.resumable),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "usage": {
            key: value
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens", "cost"}
        },
        "measurements": measurements,
        "result": run.result_payload if isinstance(run.result_payload, dict) else None,
    }


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"




def _render_transcript(
    title: str,
    messages: list[ProjectMessage],
    *,
    as_html: bool,
    include_tools: bool,
    include_timestamps: bool,
) -> str:
    lines = [f"# {title}", "", "OmicsBase workspace transcript", ""]
    for message in messages:
        if message.role == "tool" and not include_tools:
            continue
        stamp = ""
        if include_timestamps and message.created_at:
            stamp = f" ({message.created_at.isoformat()})"
        lines.append(f"## {message.role}{stamp}")
        lines.append("")
        lines.append(message.content or "")
        lines.append("")
    markdown = "\n".join(lines).rstrip() + "\n"
    if not as_html:
        return markdown
    escaped = (
        markdown.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{title} transcript</title></head><body><pre>{escaped}</pre></body></html>\n"
    )
from app.services.workspace_turn.protocol import message_payload as _message_payload
