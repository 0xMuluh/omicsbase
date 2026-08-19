"""Workspace agent execution, streaming, assistant, and job tracking endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project, ProjectMessage, UploadedFile
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
    return (
        db.query(ProjectMessage)
        .filter(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at.asc())
        .all()
    )


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
                return None
            signature = json.dumps(snapshot, sort_keys=True, default=str)
            if signature != previous_signature:
                previous_signature = signature
                return _sse_event("workspace", snapshot)
            return ""

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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Run the unified workspace agent and stream observations as NDJSON."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not data.message.strip():
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    from app.services.agent_runtime import build_run_event_metadata, record_project_message as persist_project_message
    from app.services.agent_plans import (
        append_continuation_step,
        continuation_can_resume,
        continuation_prompt,
        get_continuation_plan,
        mark_continuation_consumed,
        mark_continuation_running,
    )

    from app.services.agent_runs import (
        IdempotencyConflict,
        get_agent_run,
        is_run_task_active,
        session_factory_for,
        register_run_task,
        unregister_run_task,
        approximate_tokens,
        create_or_get_agent_run,
        record_run_telemetry,
        telemetry_from_usage,
        record_stream_event,
        replay_agent_run_stream,
        run_cancel_requested,
        serialize_agent_run,
        transition_agent_run,
        TOKEN_CHUNK_FLUSH_CHARS,
    )

    request_payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data.dict()
    try:
        run, run_created = create_or_get_agent_run(
            db,
            tenant_id=tenant_id,
            owner_id=user_id,
            surface="workspace",
            idempotency_scope=f"workspace:{project_id}:turn",
            idempotency_key=data.idempotency_key,
            request_payload=request_payload,
            project_id=str(project.id),
            run_metadata={"chat_mode": data.chat_mode},
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
        persisted_history = (
            db.query(ProjectMessage)
            .filter(
                ProjectMessage.project_id == project_id,
                ProjectMessage.id != str(user_message.id),
            )
            .order_by(ProjectMessage.created_at.asc())
            .all()
        )
    else:
        transition_agent_run(db, run, "running", event_type="run_started")
        db.commit()
        persisted_history = (
            db.query(ProjectMessage)
            .filter(ProjectMessage.project_id == project_id)
            .order_by(ProjectMessage.created_at.asc())
            .all()
        )
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
    worker_persisted_history = (
        worker_db.query(ProjectMessage)
        .filter(
            ProjectMessage.project_id == project_id,
            ProjectMessage.id != str(user_message.id),
        )
        .order_by(ProjectMessage.created_at.asc())
        .all()
    )
    request_db.close()
    db = worker_db
    project = worker_project
    run = worker_run
    user_message = worker_user_message
    persisted_history = worker_persisted_history

    async def event_stream():
        turn_started = asyncio.get_running_loop().time()
        output_chars = 0
        provider_usage: dict[str, int] = {}
        tool_started_at = {}
        token_buffer: list[str] = []
        telemetry_written = False
        waiting_for_dependency = False
        agent_memory = dict(project.agent_memory or {})
        if agent_memory.pop("pending_question", None) is not None:
            project.agent_memory = agent_memory
            db.commit()
        record_stream_event(db, run, {"type": "message", "message_id": str(user_message.id), "message": _message_payload(user_message)})
        db.commit()
        yield _ndjson_event({"type": "run", "run": serialize_agent_run(run)})
        yield _ndjson_event(
            {
                "type": "message",
                "message": _message_payload(user_message),
            }
        )

        title_task = None
        title_expected_name = str(project.name or "")
        if getattr(project, "name_source", "default") == "default":
            from app.services.home_agent import generate_project_title
            title_task = asyncio.create_task(
                generate_project_title(user_message.content or data.message)
            )

        def claim_title(new_title: str) -> str | None:
            from app.services.project_titles import claim_auto_title

            title_db = worker_session_factory()
            try:
                return claim_auto_title(
                    title_db,
                    project_id=str(project.id),
                    expected_name=title_expected_name,
                    proposed_name=new_title,
                )
            finally:
                title_db.close()

        from app.models.project import Project, ProjectMessage, UploadedFile
        from app.services.llm import resolve_target
        from app.services.opencode_client import stream_opencode
        from app.tasks.analysis import _stage_uploaded_files

        agent_provider, agent_model = resolve_target("agent")

        project_dir = Path(settings.projects_dir) / str(project.id)
        project_dir.mkdir(parents=True, exist_ok=True)
        if not project.project_dir:
            project.project_dir = str(project_dir)
        _stage_uploaded_files(
            project_dir,
            db.query(UploadedFile).filter(UploadedFile.project_id == str(project.id)).all(),
        )
        db.commit()

        prompt = data.message
        if data.chat_mode == "discuss":
            prompt = f"[discuss mode — do not modify files]\n{prompt}"

        stream_source = stream_opencode(
            project_dir=project_dir,
            instruction=prompt,
            provider=agent_provider,
            model=agent_model,
            chat_mode=data.chat_mode,
        )

        async for event in stream_source:
            event = dict(event)
            event_type = str(event.get("type") or "stream_event")
            if event_type == "usage":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    for key, value in usage.items():
                        usage_key = str(key)
                        try:
                            provider_usage[usage_key] = provider_usage.get(usage_key, 0) + int(value)
                        except (TypeError, ValueError):
                            continue
                continue
            if event_type == "token":
                token = str(event.get("token") or "")
                output_chars += len(token)
                if token:
                    token_buffer.append(token)
                    if sum(len(item) for item in token_buffer) >= TOKEN_CHUNK_FLUSH_CHARS:
                        record_stream_event(
                            db,
                            run,
                            {"type": "token_chunk", "token": "".join(token_buffer)},
                        )
                        token_buffer.clear()
            if event_type == "tool_started":
                tool_started_at[str(event.get("tool_call_id") or event.get("tool") or "unknown")] = asyncio.get_running_loop().time()
                if run.status == "running":
                    transition_agent_run(db, run, "waiting_tool", event_type="tool_waiting", payload={"tool": event.get("tool")})
            elif event_type in {"tool_completed", "execution_queued", "action_event"} and run.status == "waiting_tool":
                transition_agent_run(db, run, "running", event_type="tool_resumed")
            elif event_type == "wait":
                waiting_for_dependency = True
                if run.status in {"running", "waiting_tool"}:
                    transition_agent_run(
                        db,
                        run,
                        "paused",
                        event_type="run_waiting",
                        payload={"dependency": event.get("dependency"), "step": event.get("step")},
                    )
            memory_updates = event.pop("memory_updates", [])
            if memory_updates:
                from app.services.agent_runtime import update_durable_project_memory

                update_durable_project_memory(
                    db,
                    project,
                    memory_updates,
                    source_message=data.message,
                )
            if event["type"] == "tool_completed":
                tool_message = record_run_message(
                    db,
                    project,
                    "tool",
                    event.get("summary", "Workspace inspection completed"),
                    kind="tool",
                    metadata={
                        "tool": event.get("tool"),
                        "step": event.get("step"),
                    },
                )
                event["message_id"] = str(tool_message.id)


            if event["type"] == "final" and not event.get("message_id"):
                metadata: dict = {}
                if memory_updates:
                    metadata["memory_updates"] = len(memory_updates)
                if event.get("quick_actions"):
                    metadata["quick_actions"] = event["quick_actions"]
                if getattr(data, "chat_mode", None):
                    metadata["chat_mode"] = data.chat_mode
                if event.get("reasoning"):
                    metadata["reasoning"] = event["reasoning"]
                message = record_run_message(
                    db,
                    project,
                    "assistant",
                    event["message"],
                    metadata=metadata or None,
                )
                event["message_id"] = str(message.id)

            if event["type"] == "final" and event.get("awaiting_answer"):
                agent_memory = dict(project.agent_memory or {})
                agent_memory["pending_question"] = event["awaiting_answer"]
                project.agent_memory = agent_memory
                db.commit()

            if event.get("type") == "action_queued" and event.get("job_id"):
                append_continuation_step(
                    run,
                    action=str(event.get("action") or "workspace action"),
                    dependency_kind="job",
                    dependency_id=str(event["job_id"]),
                    instruction=str(user_message.content or data.message).strip(),
                    arguments=arguments,
                )

            event_type = str(event.get("type") or event_type)
            if event_type in {"final", "action_queued", "cancelled"} and token_buffer:
                record_stream_event(
                    db,
                    run,
                    {"type": "token_chunk", "token": "".join(token_buffer)},
                )
                token_buffer.clear()
            if event_type != "token":
                replay_event = record_stream_event(db, run, event)
                event["run_id"] = str(run.id)
                event["run_sequence"] = replay_event.sequence
            if event_type == "tool_completed":
                tool_key = str(event.get("tool_call_id") or event.get("tool") or "unknown")
                started = tool_started_at.pop(tool_key, None)
                record_run_telemetry(
                    db,
                    run,
                    kind="tool",
                    operation=str(event.get("tool") or "workspace_tool"),
                    status=str(event.get("status") or "completed"),
                    duration_ms=((asyncio.get_running_loop().time() - started) * 1000) if started else None,
                    provider=settings.llm_provider,
                    model=settings.llm_model,
                    error=event.get("summary") if event.get("status") == "error" else None,
                    metadata={"step": event.get("step"), "tool_call_id": event.get("tool_call_id")},
                )
            if event_type in {"final", "action_queued", "cancelled"}:
                cancelled = event_type == "cancelled" or run_cancel_requested(db, str(run.id))
                continuation = get_continuation_plan(run)
                waiting_for_continuation = (
                    waiting_for_dependency
                    or (
                        event_type == "action_queued"
                        and bool(continuation)
                        and continuation.get("status") in {"waiting", "ready", "failed", "running"}
                    )
                    or (
                        event_type == "final"
                        and bool(continuation)
                        and continuation.get("status") == "waiting"
                    )
                )
                if (
                    not cancelled
                    and not waiting_for_continuation
                    and continuation
                    and continuation.get("status") in {"ready", "failed", "running"}
                ):
                    consumed = mark_continuation_consumed(run)
                    if consumed:
                        continuation = consumed
                        waiting_for_continuation = continuation.get("status") in {
                            "waiting",
                            "ready",
                            "failed",
                            "running",
                        }
                        record_stream_event(
                            db,
                            run,
                            {
                                "type": "continuation_consumed",
                                "action": consumed.get("action"),
                                "step_id": consumed.get("active_step_id"),
                                "continuation_status": consumed.get("status"),
                            },
                        )
                turn_failed = (
                    event_type == "final"
                    and event.get("ok") is False
                    and not waiting_for_continuation
                )
                target_status = (
                    "cancelled"
                    if cancelled
                    else "paused"
                    if waiting_for_continuation
                    else "failed"
                    if turn_failed
                    else "completed"
                )
                if run.status not in {"completed", "failed", "cancelled"}:
                    transition_agent_run(
                        db,
                        run,
                        target_status,
                        event_type=(
                            "run_cancelled" if cancelled
                            else "run_waiting_continuation" if waiting_for_continuation
                            else "run_failed" if turn_failed
                            else "run_completed"
                        ),
                        payload={
                            "message_id": event.get("message_id"),
                            "event_type": event_type,
                            "ok": event.get("ok"),
                            "error": event.get("error"),
                        },
                    )
                run.result_payload = {
                    "message_id": event.get("message_id"),
                    "event_type": event_type,
                    "ok": event.get("ok"),
                    "error": event.get("error"),
                }
                if not telemetry_written:
                    record_run_telemetry(
                        db,
                        run,
                        kind="agent",
                        operation="workspace_turn",
                        status=(
                            "cancelled"
                            if cancelled
                            else "paused"
                            if waiting_for_continuation
                            else "failed"
                            if turn_failed
                            else "completed"
                        ),
                        duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                        provider=settings.llm_provider,
                        model=settings.llm_model,
                        **telemetry_from_usage(
                            provider_usage,
                            fallback_input_tokens=approximate_tokens(data.message),
                            fallback_output_tokens=max(0, (output_chars + 3) // 4),
                            metadata={"chat_mode": data.chat_mode},
                        ),
                    )
                    telemetry_written = True
            db.commit()
            yield _ndjson_event(event)

            if title_task and title_task.done():
                try:
                    new_title = title_task.result()
                    title_task = None
                    applied_title = claim_title(new_title)
                    if applied_title:
                        yield _ndjson_event(
                            {
                                "type": "title_update",
                                "project_id": str(project.id),
                                "name": applied_title,
                                "name_source": "auto",
                            }
                        )
                except Exception as title_err:
                    logger.warning("LLM auto-titling failed: %s", title_err)

        if title_task:
            try:
                new_title = await title_task
                applied_title = claim_title(new_title)
                if applied_title:
                    yield _ndjson_event(
                        {
                            "type": "title_update",
                            "project_id": str(project.id),
                            "name": applied_title,
                            "name_source": "auto",
                        }
                    )
            except Exception as title_err:
                logger.warning("LLM auto-titling failed: %s", title_err)

    async def consume_run():
        worker_task = asyncio.current_task()
        try:
            async for _ in event_stream():
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
    """Request cancellation of a running or pending generate job."""
    get_project_for_tenant(db, project_id, tenant_id)
    job = db.query(Job).filter(Job.id == job_id, Job.project_id == project_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in {"completed", "failed", "cancelled"}:
        return job
    job.status = "cancelled"
    db.commit()
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
                "type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "error": job.error,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
            for job in jobs
        ],
    }


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _message_payload(message: ProjectMessage) -> dict:
    return {
        "id": str(message.id),
        "project_id": str(message.project_id),
        "role": message.role,
        "kind": message.kind,
        "content": message.content,
        "metadata": message.message_metadata,
        "cell_id": str(message.cell_id) if message.cell_id else None,
        "cell_type": message.cell_type,
        "cell_revision": message.cell_revision,
        "execution_id": str(message.execution_id) if message.execution_id else None,
        "created_at": message.created_at.isoformat(),
    }


def _ndjson_event(event: dict) -> str:
    return json.dumps(event, default=str) + "\n"


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
