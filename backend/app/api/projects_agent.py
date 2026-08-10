"""Workspace agent execution, streaming, assistant, and job tracking endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id, get_project_for_tenant
from app.config import settings
from app.database import get_db
from app.models.project import Job, Project, ProjectMessage, UploadedFile
from app.schemas.schemas import (
    AssistantRequest,
    AssistantResponse,
    JobOut,
    ProjectMessageOut,
    WorkspaceAgentRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{project_id}/assistant", response_model=AssistantResponse, deprecated=True)
async def assistant_message(
    project_id: str,
    data: AssistantRequest,
    response: Response,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Compatibility endpoint; new clients should use the unified agent stream."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f"</projects/{project_id}/agent/stream>; rel=\"successor-version\""

    from app.services.assistant import respond_to_prompt

    result = await respond_to_prompt(project, data.message, history=data.history)
    return AssistantResponse(
        type=result["type"],
        message=result["message"],
        instruction=result.get("instruction"),
    )


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
    from app.services.workspace_agent import stream_workspace_agent
    from app.services.agent_plans import (
        attach_continuation_plan,
        build_continuation_plan,
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

        def inline_action_handler(action: str, arguments: dict):
            from app.services.agent_runtime import record_agent_action
            from app.services.data_acquisition import fetch_url_into_study, import_package_dataset

            if action == "import_package_data":
                result = import_package_dataset(
                    db,
                    project,
                    package=str(arguments.get("package") or ""),
                    dataset=str(arguments.get("dataset") or ""),
                    role=str(arguments.get("role") or "auto"),
                )
            elif action == "fetch_url":
                result = fetch_url_into_study(
                    db,
                    project,
                    url=str(arguments.get("url") or ""),
                    filename=arguments.get("filename"),
                    role=str(arguments.get("role") or "auto"),
                )
            else:
                result = {"status": "error", "error": f"Unsupported inline action: {action}"}

            db.refresh(project)
            status = "completed" if result.get("status") != "error" else "failed"
            record_agent_action(
                db,
                project,
                action,
                status,
                (
                    f"{action} completed"
                    if status == "completed"
                    else f"{action} failed: {result.get('error', 'unknown error')}"
                ),
                {"arguments": arguments, "result": result},
            )
            return result

        def knowledge_search_handler(arguments: dict):
            from app.services.bioc_knowledge import search_bioc_knowledge

            try:
                limit = max(1, min(8, int(arguments.get("limit") or 5)))
            except (TypeError, ValueError):
                limit = 5
            return search_bioc_knowledge(
                db,
                str(arguments.get("query") or ""),
                channel=str(arguments.get("channel") or "stable"),
                limit=limit,
                source_slug=arguments.get("book") or None,
            )

        async for event in stream_workspace_agent(
            project,
            agent_request,
            persisted_history,
            inline_action_handler=inline_action_handler,
            knowledge_search_handler=knowledge_search_handler,
            cancel_check=lambda: run_cancel_requested(db, str(run.id)),
        ):
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

            if event["type"] == "action":
                action = event["action"]
                arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
                instruction = event.get("instruction") or data.message.strip()
                mutation_authorized = event.get("mutation_authorized") is True
                if not mutation_authorized:
                    event = {
                        "type": "final",
                        "message": (
                            "I inspected the request but did not start or change the analysis "
                            "because no explicit workspace mutation was authorized."
                        ),
                    }
                elif project.status in {"planning", "generating", "rendering", "repairing", "reviewing", "editing"}:
                    from app.services.agent_runtime import queue_pending_guidance

                    guidance = instruction or data.message.strip()
                    queued = queue_pending_guidance(
                        db,
                        project,
                        guidance,
                        source=f"action:{action}",
                        mutation_authorized=True,
                    )
                    message_text = (
                        "The workspace is busy with another job, so I queued your guidance "
                        f"for after it finishes: {queued['content']}"
                    )
                    message = record_run_message(
                        db,
                        project,
                        "assistant",
                        message_text,
                        metadata={
                            "action": "queue_guidance",
                            "queued_action": action,
                            "pending_guidance": queued,
                        },
                    )
                    event = {
                        "type": "final",
                        "message": message_text,
                        "message_id": str(message.id),
                    }
                elif action == "edit_project":
                    if not project.project_dir:
                        event = {
                            "type": "final",
                            "message": "There is no generated workspace to edit yet. Build the project first.",
                        }
                    else:
                        job = _queue_agent_edit(
                            db,
                            project,
                            instruction,
                            background_tasks,
                        )
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            event["message"],
                            metadata={"action": action, "job_id": str(job.id)},
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": event["message"],
                            "message_id": str(message.id),
                        }
                elif action == "plan_analysis":
                    try:
                        job = _queue_agent_planning(db, project, background_tasks)
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            event["message"],
                            metadata={"action": action, "job_id": str(job.id)},
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": event["message"],
                            "message_id": str(message.id),
                        }
                    except ValueError as exc:
                        event = {"type": "final", "message": str(exc)}
                elif action == "render_report":
                    if not project.project_dir:
                        event = {
                            "type": "final",
                            "message": "There is no generated workspace to render yet.",
                        }
                    else:
                        job = _queue_agent_render(db, project, background_tasks)
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            event["message"],
                            metadata={"action": action, "job_id": str(job.id)},
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": event["message"],
                            "message_id": str(message.id),
                        }
                elif action == "repair_report":
                    if not project.project_dir:
                        event = {
                            "type": "final",
                            "message": "There is no generated workspace to repair yet.",
                        }
                    else:
                        job = _queue_agent_render(db, project, background_tasks)
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            event["message"],
                            metadata={"action": action, "job_id": str(job.id)},
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": event["message"],
                            "message_id": str(message.id),
                        }
                elif action == "run_recipe":
                    recipe_id = str(arguments.get("recipe_id") or "")
                    try:
                        job = _queue_agent_recipe(
                            db,
                            project,
                            recipe_id,
                            background_tasks,
                        )
                        message_text = f"I’m running {recipe_id} and only its stale dependencies."
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            message_text,
                            metadata={
                                "action": action,
                                "recipe_id": recipe_id,
                                "job_id": str(job.id),
                            },
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": message_text,
                            "message_id": str(message.id),
                        }
                    except ValueError as exc:
                        event = {"type": "final", "message": str(exc)}
                elif action == "undo_project_edit":
                    transaction_id = str(arguments.get("transaction_id") or "").strip()
                    if not project.project_dir:
                        event = {"type": "final", "message": "There is no generated workspace whose edit history can be undone."}
                    elif not transaction_id:
                        event = {"type": "final", "message": "Undo requires a transaction_id from the workspace edit history."}
                    else:
                        try:
                            from app.services.edit_engine import EditBusy, EditEngineError, revert_transaction
                            result = revert_transaction(project.project_dir, transaction_id, lock_timeout=0)
                            from app.services.agent_runtime import refresh_project_memory, record_agent_action
                            from app.services.project_edit_index import record_project_edit
                            record_project_edit(db, project, result)
                            refresh_project_memory(db, project)
                            record_agent_action(
                                db,
                                project,
                                "file_edit",
                                "completed",
                                f"Undid edit transaction {transaction_id}",
                                {"transaction_id": transaction_id, "revert_transaction_id": result.transaction_id},
                                files=[item.path for item in result.files],
                            )
                            event = {
                                "type": "final",
                                "message": f"Undid edit transaction {transaction_id} with a new hash-checked transaction.",
                                "details": result.to_dict(),
                            }
                        except EditBusy as exc:
                            event = {"type": "final", "message": f"Undo is temporarily blocked because the workspace is busy: {exc}"}
                        except EditEngineError as exc:
                            event = {"type": "final", "message": f"Undo was not applied: {exc}"}
                elif action == "run_analysis":
                    try:
                        job = _queue_agent_generation(
                            db,
                            project,
                            background_tasks,
                            resume_from_checkpoint=bool(arguments.get("resume_from_checkpoint", True)),
                        )
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            event["message"],
                            metadata={"action": action, "job_id": str(job.id)},
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": event["message"],
                            "message_id": str(message.id),
                        }
                    except ValueError as exc:
                        event = {"type": "final", "message": str(exc)}
                elif action in {
                    "set_recipe_enabled",
                    "update_recipe_parameters",
                    "set_analysis_variables",
                    "rollback_analysis_configuration",
                }:
                    try:
                        mutation = _apply_agent_configuration(
                            db,
                            project,
                            action,
                            arguments,
                        )
                        target_recipe_id = None
                        if action == "update_recipe_parameters" and arguments.get("recipe_id"):
                            target_recipe_id = str(arguments["recipe_id"])
                        elif (
                            action == "set_recipe_enabled"
                            and arguments.get("enabled") is True
                            and arguments.get("recipe_id")
                        ):
                            target_recipe_id = str(arguments["recipe_id"])
                        job = _queue_agent_generation(
                            db,
                            project,
                            background_tasks,
                            target_recipe_id=target_recipe_id,
                        )
                        run_scope = (
                            f"I’m rerunning {target_recipe_id} and only its stale dependencies"
                            if target_recipe_id
                            else "I’m rebuilding the affected report configuration"
                        )
                        message_text = f"{mutation['summary']}. {run_scope}, then validating the report."
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            message_text,
                            metadata={
                                "action": action,
                                "arguments": arguments,
                                "job_id": str(job.id),
                            },
                        )
                        event = {
                            "type": "action_queued",
                            "action": action,
                            "job_id": str(job.id),
                            "message": message_text,
                            "message_id": str(message.id),
                        }
                    except ValueError as exc:
                        event = {
                            "type": "final",
                            "message": f"I did not change the analysis configuration: {exc}",
                        }
                elif action == "queue_guidance":
                    from app.services.agent_runtime import queue_pending_guidance

                    guidance = str(arguments.get("guidance") or instruction or data.message).strip()
                    try:
                        queued = queue_pending_guidance(
                            db,
                            project,
                            guidance,
                            source="agent",
                            mutation_authorized=True,
                        )
                        message_text = (
                            f"Queued for after the current job: {queued['content']}"
                        )
                        message = record_run_message(
                            db,
                            project,
                            "assistant",
                            message_text,
                            metadata={"action": action, "pending_guidance": queued},
                        )
                        event = {
                            "type": "final",
                            "message": message_text,
                            "message_id": str(message.id),
                        }
                    except ValueError as exc:
                        event = {"type": "final", "message": str(exc)}

            if event["type"] == "final" and not event.get("message_id"):
                metadata: dict = {}
                if memory_updates:
                    metadata["memory_updates"] = len(memory_updates)
                if event.get("quick_actions"):
                    metadata["quick_actions"] = event["quick_actions"]
                if getattr(data, "chat_mode", None):
                    metadata["chat_mode"] = data.chat_mode
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
                plan = build_continuation_plan(
                    run,
                    action=str(event.get("action") or "workspace action"),
                    dependency_kind="job",
                    dependency_id=str(event["job_id"]),
                    instruction=str(user_message.content or data.message).strip(),
                    arguments=arguments,
                )
                attach_continuation_plan(run, plan)

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
                )
                target_status = "cancelled" if cancelled else ("paused" if waiting_for_continuation else "completed")
                if run.status not in {"completed", "failed", "cancelled"}:
                    transition_agent_run(
                        db,
                        run,
                        target_status,
                        event_type=(
                            "run_cancelled" if cancelled
                            else "run_waiting_continuation" if waiting_for_continuation
                            else "run_completed"
                        ),
                        payload={"message_id": event.get("message_id"), "event_type": event_type},
                    )
                run.result_payload = {"message_id": event.get("message_id"), "event_type": event_type}
                if not telemetry_written:
                    record_run_telemetry(
                        db,
                        run,
                        kind="agent",
                        operation="workspace_turn",
                        status=("cancelled" if cancelled else "paused" if waiting_for_continuation else "completed"),
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


def _queue_agent_planning(
    db: Session,
    project: Project,
    background_tasks: BackgroundTasks,
) -> Job:
    from app.api.projects_pipeline import _dispatch_task
    from app.services.agent_runtime import record_agent_action, set_agent_state
    from app.tasks.analysis import run_planning

    files = db.query(UploadedFile).filter(UploadedFile.project_id == project.id).all()
    data_files = [f for f in files if f.file_role != "analysis_plan"]
    if not data_files:
        raise ValueError(
            "No study data files are registered yet. Import a package dataset or fetch a URL first."
        )

    job = Job(project_id=project.id, job_type="plan", status="pending")
    db.add(job)
    project.status = "planning"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "planning", "Planning analysis from acquired study inputs")
    record_agent_action(
        db,
        project,
        "plan",
        "started",
        "Workspace agent requested analysis planning",
        job_id=str(job.id),
    )
    _dispatch_task(
        run_planning,
        project,
        job,
        db,
        background_tasks,
        task_kwargs={"allow_auto_build": False},
    )
    return job


def _queue_agent_edit(
    db: Session,
    project: Project,
    instruction: str,
    background_tasks: BackgroundTasks,
) -> Job:
    from app.services.agent_runtime import record_agent_action, set_agent_state
    from app.tasks.analysis import run_editing

    job = Job(project_id=project.id, job_type="edit", status="pending")
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "editing", "Editing generated source", {"instruction": instruction})
    record_agent_action(
        db,
        project,
        "edit",
        "started",
        "Workspace agent is editing generated source",
        {"instruction": instruction},
        job_id=str(job.id),
    )
    if settings.task_backend.lower() == "celery":
        run_editing.delay(str(project.id), str(job.id), instruction=instruction)
    elif settings.task_backend.lower() == "background":
        background_tasks.add_task(run_editing, str(project.id), str(job.id), instruction=instruction)
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported task backend: {settings.task_backend}")
    return job


def _queue_agent_render(
    db: Session,
    project: Project,
    background_tasks: BackgroundTasks,
) -> Job:
    from app.api.projects_pipeline import _dispatch_task
    from app.services.agent_runtime import record_agent_action, set_agent_state
    from app.tasks.analysis import run_rendering

    job = Job(project_id=project.id, job_type="render", status="pending")
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "rendering", "Rendering report")
    record_agent_action(db, project, "render", "started", "Workspace agent requested a render", job_id=str(job.id))
    _dispatch_task(run_rendering, project, job, db, background_tasks)
    return job


def _queue_agent_generation(
    db: Session,
    project: Project,
    background_tasks: BackgroundTasks,
    target_recipe_id: str | None = None,
    resume_from_checkpoint: bool = True,
) -> Job:
    if not project.analysis_plan:
        raise ValueError("The project has no analysis plan to execute.")

    from app.services.agent_runtime import record_agent_action, set_agent_state
    from app.tasks.analysis import run_generation

    job = Job(project_id=project.id, job_type="generate", status="pending")
    db.add(job)
    project.status = "generating"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "generating", "Rebuilding configured analysis graph")
    record_agent_action(
        db,
        project,
        "generate",
        "started",
        "Workspace agent is rebuilding the configured analysis graph",
        job_id=str(job.id),
    )
    if settings.task_backend.lower() == "celery":
        run_generation.delay(
            str(project.id),
            str(job.id),
            target_recipe_id=target_recipe_id,
            resume_from_checkpoint=resume_from_checkpoint,
        )
    elif settings.task_backend.lower() == "background":
        background_tasks.add_task(
            run_generation,
            str(project.id),
            str(job.id),
            target_recipe_id=target_recipe_id,
            resume_from_checkpoint=resume_from_checkpoint,
        )
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported task backend: {settings.task_backend}")
    return job


def _queue_agent_recipe(
    db: Session,
    project: Project,
    recipe_id: str,
    background_tasks: BackgroundTasks,
) -> Job:
    if not recipe_id:
        raise ValueError("run_recipe requires recipe_id.")
    enabled = {
        step.get("recipe_id")
        for step in (project.analysis_plan or {}).get("workflow", [])
        if step.get("enabled")
    }
    inventory_id = f"{(project.analysis_plan or {}).get('domain')}.inventory"
    if recipe_id != inventory_id and recipe_id not in enabled:
        raise ValueError(f"Recipe is not enabled in the current analysis plan: {recipe_id}")
    if not project.project_dir:
        raise ValueError("The project has no generated workspace to execute.")

    from app.services.agent_runtime import record_agent_action, set_agent_state
    from app.services.recipe_registry import get_recipe
    from app.tasks.analysis import run_recipe_execution

    if not get_recipe(recipe_id):
        raise ValueError(f"Unknown recipe: {recipe_id}")
    job = Job(
        project_id=project.id,
        job_type="recipe",
        status="pending",
        progress=[{"target_recipe_id": recipe_id}],
    )
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "rendering", f"Running targeted recipe {recipe_id}")
    record_agent_action(
        db,
        project,
        "recipe",
        "started",
        f"Queued targeted recipe {recipe_id}",
        {"recipe_id": recipe_id},
        job_id=str(job.id),
    )
    if settings.task_backend.lower() == "celery":
        run_recipe_execution.delay(str(project.id), str(job.id), recipe_id=recipe_id)
    elif settings.task_backend.lower() == "background":
        background_tasks.add_task(
            run_recipe_execution,
            str(project.id),
            str(job.id),
            recipe_id=recipe_id,
        )
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported task backend: {settings.task_backend}")
    return job


def _apply_agent_configuration(
    db: Session,
    project: Project,
    action: str,
    arguments: dict,
) -> dict:
    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    from app.services.analysis_configuration import apply_analysis_configuration

    mutation = apply_analysis_configuration(project, action, arguments)
    project.analysis_plan = mutation["plan"]
    db.commit()
    refresh_project_memory(db, project)
    record_agent_action(
        db,
        project,
        "analysis_config",
        "completed",
        mutation["summary"],
        {
            "operation": action,
            "arguments": arguments,
            "previous_plan": mutation["previous_plan"],
        },
    )
    return mutation


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
