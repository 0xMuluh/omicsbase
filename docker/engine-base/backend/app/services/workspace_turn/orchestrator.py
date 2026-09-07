"""Application orchestration for a durable workspace-agent turn."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_project_for_tenant
from app.models.project import ProjectMessage
from app.schemas.schemas import WorkspaceAgentRequest
from app.services.workspace_turn.events import stream_workspace_events
from app.services.workspace_turn.protocol import ndjson_event as _ndjson_event

logger = logging.getLogger(__name__)


async def run_workspace_turn(
    project_id: str,
    data: WorkspaceAgentRequest,
    db: Session,
    tenant_id: str,
    user_id: str,
):
    """Run the unified workspace agent and stream observations as NDJSON."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if (project.agent_memory or {}).get("pending_clarifications"):
        raise HTTPException(
            status_code=409,
            detail="Answer the pending clarification before starting another workspace turn",
        )
    if not data.message.strip():
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    
    from app.services.agent_runtime import record_project_message as persist_project_message
    from app.services.agent_plans import (
        continuation_can_resume,
        continuation_prompt,
        get_continuation_plan,
        mark_continuation_running,
    )
    
    from app.services.agent_runs import (
        IdempotencyConflict,
        get_agent_run,
        is_run_task_active,
        session_factory_for,
        register_run_task,
        unregister_run_task,
        create_or_get_agent_run,
        replay_agent_run_stream,
        transition_agent_run,
    )
    
    from app.services.coding_agents import get_coding_agent_backend

    try:
        selected_backend = get_coding_agent_backend(data.agent_backend).name
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request_payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data.dict()
    workspace_action = str(getattr(data, "action", "") or "").strip().lower() or None
    run_metadata = {"chat_mode": data.chat_mode, "agent_backend": selected_backend}
    if workspace_action:
        run_metadata["job_type"] = workspace_action
    try:
        run, run_created = create_or_get_agent_run(
            db,
            tenant_id=tenant_id,
            owner_id=user_id,
            surface="workspace",
            idempotency_scope=f"workspace:{project_id}:turn",
            kind=f"workspace_{workspace_action}" if workspace_action else "agent_turn",
            idempotency_key=data.idempotency_key,
            request_payload=request_payload,
            project_id=str(project.id),
            run_metadata=run_metadata,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    
    if run_created:
        from app.models.project import Job, Project
        from app.models.runs import AgentRun

        db.query(Project.id).filter(Project.id == str(project.id)).with_for_update().first()
        active_job = (
            db.query(Job)
            .filter(
                Job.project_id == str(project.id),
                Job.agent_run_id.is_(None),
                Job.status.in_({"pending", "running", "cancel_requested"}),
            )
            .order_by(Job.created_at.desc())
            .first()
        )
        active_run = (
            db.query(AgentRun)
            .filter(
                AgentRun.project_id == str(project.id),
                AgentRun.id != str(run.id),
                AgentRun.status.in_({"queued", "running", "waiting_tool", "cancel_requested"}),
            )
            .order_by(AgentRun.created_at.desc())
            .first()
        )
        if active_job is not None or active_run is not None:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "This project already has an active operation",
                    "job_id": str(active_job.id) if active_job is not None else None,
                    "agent_run_id": (
                        str(active_job.agent_run_id)
                        if active_job is not None and active_job.agent_run_id
                        else str(active_run.id) if active_run is not None else None
                    ),
                    "status": active_job.status if active_job is not None else active_run.status,
                },
            )

    continuation_plan = get_continuation_plan(run)
    continuation_resume = (
        not run_created
        and continuation_can_resume(run)
        and not is_run_task_active(str(run.id))
    )
    resume_existing_run = (
        not run_created
        and not is_run_task_active(str(run.id))
        and bool(run.resumable)
        and (continuation_resume or (run.status == "paused" and not continuation_plan))
    )
    if not run_created and not resume_existing_run:
        async def replay_existing_run():
            async for replay_event in replay_agent_run_stream(
                str(run.id),
                tenant_id,
                session_factory=session_factory_for(db),
            ):
                yield _ndjson_event(replay_event)
    
        return StreamingResponse(
            replay_existing_run(),
            media_type="application/x-ndjson",
            headers={"X-Agent-Run-ID": str(run.id), "X-Agent-Run-Replayed": "true"},
        )
    
    execution_id = str(run.id)
    
    def record_run_message(
        message_db,
        message_project,
        role: str,
        content: str,
        *,
        kind: str = "message",
        metadata: dict | None = None,
    ):
        """Persist stream messages with a forward-compatible cell envelope."""
        return persist_project_message(
            message_db,
            message_project,
            role,
            content,
            kind=kind,
            metadata=metadata,
            cell_id=str(uuid.uuid4()),
            cell_revision=1,
            execution_id=execution_id,
        )
    
    if resume_existing_run:
        if continuation_resume and continuation_plan:
            mark_continuation_running(run)
        transition_agent_run(db, run, "running", event_type="run_resumed")
        run.result_payload = None
        db.commit()
        user_message = (
            db.query(ProjectMessage)
            .filter(
                ProjectMessage.project_id == project_id,
                ProjectMessage.execution_id == execution_id,
                ProjectMessage.role == "user",
            )
            .order_by(ProjectMessage.created_at.asc())
            .first()
        )
        if user_message is None:
            raise HTTPException(status_code=409, detail="Paused run has no persisted user message")
    else:
        transition_agent_run(db, run, "running", event_type="run_started")
        db.commit()
        user_message = record_run_message(
            db,
            project,
            "user",
            data.message.strip(),
            metadata={
                "selected_file": data.selected_file,
                "selected_content_dirty": data.selected_content_dirty,
                "preview_path": data.preview_path,
                "chat_mode": getattr(data, "chat_mode", None) or "build",
                "action": getattr(data, "action", None),
                "attachments": [
                    attachment.model_dump(mode="json", exclude_none=True)
                    for attachment in data.attachments
                ],
            },
        )
    
    agent_request = data
    if continuation_resume and continuation_plan:
        resume_message = continuation_prompt(continuation_plan)
        if hasattr(data, "model_copy"):
            agent_request = data.model_copy(update={"message": resume_message})
        else:
            agent_request = data.copy(update={"message": resume_message})
    
    request_db = db
    worker_session_factory = session_factory_for(request_db)
    worker_db = worker_session_factory()
    worker_project = get_project_for_tenant(worker_db, project_id, tenant_id)
    worker_run = get_agent_run(worker_db, str(run.id), tenant_id)
    if worker_run is None:
        worker_db.close()
        raise HTTPException(status_code=404, detail="Agent run disappeared before execution")
    worker_user_message = (
        worker_db.query(ProjectMessage)
        .filter(ProjectMessage.id == str(user_message.id))
        .one()
    )
    request_db.close()
    db = worker_db
    project = worker_project
    run = worker_run
    user_message = worker_user_message
    
    event_stream = stream_workspace_events(
        db=db,
        run=run,
        project_id=project_id,
        tenant_id=tenant_id,
        project=project,
        data=agent_request,
        user_message=user_message,
        worker_session_factory=worker_session_factory,
        record_run_message=record_run_message,
    )

    async def consume_run():
        worker_task = asyncio.current_task()
        try:
            async for _ in event_stream:
                pass
        except asyncio.CancelledError:
            pause_db = worker_session_factory()
            try:
                paused_run = get_agent_run(pause_db, str(run.id), tenant_id)
                if paused_run and paused_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if paused_run.cancel_requested else "paused"
                    if paused_run.status != target:
                        transition_agent_run(
                            pause_db,
                            paused_run,
                            target,
                            event_type="run_cancelled" if target == "cancelled" else "run_paused",
                            payload={"reason": "worker interrupted"},
                        )
                    pause_db.commit()
            finally:
                pause_db.close()
            raise
        except Exception as exc:
            logger.exception("Workspace agent worker failed: %s", exc)
            failure_db = worker_session_factory()
            try:
                failed_run = get_agent_run(failure_db, str(run.id), tenant_id)
                if failed_run and failed_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if failed_run.cancel_requested else "failed"
                    transition_agent_run(
                        failure_db,
                        failed_run,
                        target,
                        event_type="run_cancelled" if target == "cancelled" else "run_failed",
                        payload={"error": str(exc)[:1000]},
                    )
                    failure_db.commit()
            finally:
                failure_db.close()
        finally:
            try:
                close_stream = getattr(event_stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            except Exception:
                logger.debug("Workspace event stream close failed", exc_info=True)
            unregister_run_task(str(run.id), worker_task)
            resume_db = worker_session_factory()
            try:
                from app.services.agent_continuations import dispatch_ready_continuations
    
                dispatch_ready_continuations(resume_db, run_id=str(run.id))
            finally:
                resume_db.close()
            worker_db.close()
    
    worker_task = asyncio.create_task(consume_run(), name=f"workspace-agent-{run.id}")
    register_run_task(str(run.id), worker_task)
    
    async def durable_stream():
        async for replay_event in replay_agent_run_stream(
            str(run.id),
            tenant_id,
            session_factory=worker_session_factory,
        ):
            yield _ndjson_event(replay_event)
    
    return StreamingResponse(
        durable_stream(),
        media_type="application/x-ndjson",
        headers={
            "X-Agent-Run-ID": str(run.id),
            "X-Agent-Run-Transport": "durable-replay",
        },
    )
