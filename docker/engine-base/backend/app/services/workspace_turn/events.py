"""Durable workspace-agent event streaming and telemetry."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.services.agent_plans import append_continuation_step, get_continuation_plan, mark_continuation_consumed
from app.services.agent_runs import (
    approximate_tokens,
    record_run_telemetry,
    record_stream_event,
    run_cancel_requested,
    serialize_agent_run,
    telemetry_from_usage,
    sync_compatibility_jobs,
    sync_project_from_agent_run,
    transition_agent_run,
)
from app.services.workspace_turn.protocol import (
    message_payload as _message_payload,
    ndjson_event as _ndjson_event,
)


logger = logging.getLogger(__name__)
_WORKSPACE_TOKEN_CHUNK_FLUSH_CHARS = 2048


def _normalise_usage(usage) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "input", "prompt_tokens", "prompt"),
        "output_tokens": ("output_tokens", "output", "completion_tokens", "completion"),
        "cached_input_tokens": ("cached_input_tokens", "cached_input"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoning_output"),
        "total_tokens": ("total_tokens", "total"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                result[target] = max(0, int(float(value)))
            except (TypeError, ValueError):
                pass
            break
    if not result.get("total_tokens") and (result.get("input_tokens") or result.get("output_tokens")):
        result["total_tokens"] = result.get("input_tokens", 0) + result.get("output_tokens", 0)
    return result


def _tool_event_details(event: dict[str, Any]) -> tuple[str, str, str, str]:
    """Normalize tool completion shapes from native and legacy adapters."""
    action = event.get("event") if isinstance(event.get("event"), dict) else {}
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    tool_name = str(
        event.get("tool")
        or action.get("tool")
        or target.get("tool")
        or action.get("title")
        or "workspace_tool"
    )
    tool_call_id = str(
        event.get("tool_call_id")
        or action.get("tool_call_id")
        or action.get("callID")
        or action.get("id")
        or tool_name
    )
    status = str(event.get("status") or action.get("status") or "completed")
    summary = str(event.get("summary") or action.get("summary") or action.get("title") or "")
    return tool_call_id, tool_name, status, summary


def _is_render_or_compute_tool(tool_name: str, summary: str) -> bool:
    text = f"{tool_name} {summary}".lower()
    return any(marker in text for marker in ("quarto", "render", "rscript", "execution", "analysis", "compute"))


async def stream_workspace_events(
    *,
    db,
    run,
    project_id: str,
    tenant_id: str,
    project,
    data,
    user_message,
    worker_session_factory,
    record_run_message,
    session_id: str | None = None,
    fresh_session: bool = False,
    cancel_check: Callable[[], bool] | None = None,
):
    loop_clock = asyncio.get_running_loop()
    turn_started = loop_clock.time()
    output_chars = 0
    token_buffer: list[str] = []
    token_buffer_chars = 0
    assistant_text_parts: list[str] = []
    assistant_message_id: str | None = None
    provider_usage: dict[str, Any] = {}
    tool_started_at: dict[str, float] = {}
    tool_names: dict[str, str] = {}
    tool_steps: dict[str, int | None] = {}
    step_started_at: dict[int, float] = {}
    step_tool_time_ms: dict[int, float] = {}
    dependency_wait_started: float | None = None
    last_meaningful_at = turn_started
    measurements: dict[str, Any] = {
        "model_calls": 0,
        "tool_calls": 0,
        "model_time_ms": 0.0,
        "tool_time_ms": 0.0,
        "render_time_ms": 0.0,
        "dependency_wait_ms": 0.0,
        "quiet_wait_ms": 0.0,
        "time_to_first_event_ms": None,
        "time_to_first_output_ms": None,
    }
    telemetry_written = False
    waiting_for_dependency = False

    def persist_measurements() -> None:
        metadata = dict(run.run_metadata or {})
        metadata["measurements"] = dict(measurements)
        run.run_metadata = metadata

    def persist_streamed_assistant(
        *,
        partial: bool,
        content: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ):
        """Create or update one durable assistant row for streamed output."""
        nonlocal assistant_message_id
        text = str(content if content is not None else "".join(assistant_text_parts)).strip()
        if not text:
            return None
        metadata: dict[str, Any] = {"streamed": True}
        if partial:
            metadata["partial"] = True
        if extra_metadata:
            metadata.update(extra_metadata)

        message = None
        if assistant_message_id:
            message = (
                db.query(ProjectMessage)
                .filter(ProjectMessage.id == assistant_message_id)
                .one_or_none()
            )
        if message is None:
            message = record_run_message(
                db,
                project,
                "assistant",
                text,
                metadata=metadata,
            )
            assistant_message_id = str(message.id)
            return message

        message.content = text
        current_metadata = message.message_metadata if isinstance(message.message_metadata, dict) else {}
        current_metadata.update(metadata)
        if not partial:
            current_metadata.pop("partial", None)
        message.message_metadata = current_metadata
        db.flush()
        return message
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
        from app.models.project import UploadedFile
        from app.services.titles import generate_title, title_context

        title_files = (
            db.query(UploadedFile)
            .filter(UploadedFile.project_id == str(project.id))
            .all()
        )
        title_task = asyncio.create_task(
            generate_title(
                kind="project",
                user_intent=(project.question or user_message.content or data.message),
                context=title_context(
                    project.custom_plan_text,
                    project.notes,
                    file_names=[str(item.original_name or "") for item in title_files],
                ),
            )
        )

    def claim_title(new_title: str) -> str | None:
        from app.services.titles import claim_project_auto_title as claim_auto_title

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

    from app.models.project import ProjectMessage, UploadedFile
    from app.services.llm import resolve_target
    from app.services.coding_agents import stream_coding_agent
    from app.services.opencode.prompts import workspace_request_context
    from app.tasks.analysis import _stage_uploaded_files

    selected_backend = str(getattr(data, "agent_backend", None) or "").strip().lower()
    if not selected_backend:
        from app.services.coding_agents import get_coding_agent_backend

        selected_backend = get_coding_agent_backend().name
    if selected_backend == "codex":
        agent_provider = "openai"
        agent_model = str(getattr(settings, "codex_model", "") or "codex-default")
    else:
        agent_provider, agent_model = resolve_target("agent")

    def cancel_requested_now() -> bool:
        if cancel_check is not None:
            try:
                return bool(cancel_check())
            except Exception:
                logger.exception("Workspace cancellation check failed")
                return False
        check_db = worker_session_factory()
        try:
            return run_cancel_requested(check_db, str(run.id))
        finally:
            check_db.close()

    def codex_user_input_check(request_id: str):
        check_db = worker_session_factory()
        try:
            from app.services.agent_runs import get_agent_run

            current_run = get_agent_run(check_db, str(run.id), tenant_id)
            metadata = current_run.run_metadata if current_run and isinstance(current_run.run_metadata, dict) else {}
            response = metadata.get("codex_user_input_response")
            if not isinstance(response, dict):
                return None
            if str(response.get("request_id") or "") != str(request_id):
                return None
            answers = response.get("answers")
            return {"answers": answers} if isinstance(answers, dict) else None
        finally:
            check_db.close()

    def codex_approval_check(request_id: str):
        check_db = worker_session_factory()
        try:
            from app.services.agent_runs import get_agent_run

            current_run = get_agent_run(check_db, str(run.id), tenant_id)
            metadata = current_run.run_metadata if current_run and isinstance(current_run.run_metadata, dict) else {}
            response = metadata.get("codex_approval_response")
            if not isinstance(response, dict):
                return None
            if str(response.get("request_id") or "") != str(request_id):
                return None
            decision = str(response.get("decision") or "").strip()
            return {"decision": decision} if decision else None
        finally:
            check_db.close()

    project_dir = Path(settings.projects_dir) / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)
    if not project.project_dir:
        project.project_dir = str(project_dir)
    uploaded = db.query(UploadedFile).filter(UploadedFile.project_id == str(project.id)).all()
    attachment_ids = {str(item.id) for item in data.attachments if item.id}
    attachment_names = {str(item.name) for item in data.attachments}
    turn_uploads = [
        item
        for item in uploaded
        if str(item.id) in attachment_ids or str(item.original_name or "") in attachment_names
    ]
    _stage_uploaded_files(
        project_dir,
        uploaded,
    )
    db.commit()

    prompt = data.message
    if data.chat_mode == "discuss":
        prompt = f"[discuss mode — do not modify files]\n{prompt}"

    stream_source = stream_coding_agent(
        project_dir=project_dir,
        instruction=prompt,
        provider=agent_provider,
        model=agent_model,
        backend=selected_backend,
        session_id=session_id,
        fresh_session=fresh_session or getattr(data, "action", None) == "generate",
        chat_mode=data.chat_mode,
        cancel_check=cancel_requested_now,
        user_input_check=codex_user_input_check,
        approval_check=codex_approval_check,
        selected_file=data.selected_file,
        selected_content=data.selected_content,
        selected_content_dirty=data.selected_content_dirty,
        preview_path=data.preview_path,
        **workspace_request_context(project, project_dir, files=turn_uploads),
    )

    try:
        async for event in stream_source:
            event = dict(event)
            event_type = str(event.get("type") or "stream_event")
            event_now = loop_clock.time()
            if event_type not in {"session", "heartbeat", "context_snapshot", "reasoning_token"}:
                if measurements["time_to_first_event_ms"] is None:
                    measurements["time_to_first_event_ms"] = max(0.0, (event_now - turn_started) * 1000.0)
                if event_type != "wait" and dependency_wait_started is not None:
                    measurements["dependency_wait_ms"] = float(measurements.get("dependency_wait_ms", 0.0)) + max(0.0, (event_now - dependency_wait_started) * 1000.0)
                    dependency_wait_started = None
                elif event_type != "wait" and not step_started_at and not tool_started_at:
                    measurements["quiet_wait_ms"] = float(measurements.get("quiet_wait_ms", 0.0)) + max(0.0, (event_now - last_meaningful_at) * 1000.0)
                last_meaningful_at = event_now
            event["type"] = event_type
            if event_type == "reasoning_token":
                continue
            if event_type == "final":
                event.pop("reasoning", None)
                if str(event.get("message") or "").strip() and measurements["time_to_first_output_ms"] is None:
                    measurements["time_to_first_output_ms"] = max(0.0, (event_now - turn_started) * 1000.0)
            if event_type == "context_snapshot":
                for key in (
                    "session_message_count",
                    "session_history_chars",
                    "session_history_tokens_estimate",
                    "system_prompt_chars",
                    "prompt_chars",
                    "prompt_tokens_estimate",
                    "attachment_count",
                    "attachment_payload_chars",
                    "estimated_context_tokens",
                ):
                    value = event.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        measurements[key] = max(0, int(value))
                persist_measurements()
            if event_type == "step_started":
                step = event.get("step")
                if isinstance(step, int) and step not in step_started_at:
                    step_started_at[step] = event_now
                    measurements["model_calls"] = int(measurements.get("model_calls", 0)) + 1
            if isinstance(event.get("step"), int):
                run.current_step = max(int(run.current_step or 0), int(event["step"]))
            if event_type == "session":
                session_id = str(event.get("session_id") or "").strip()
                if session_id:
                    metadata = dict(run.run_metadata or {})
                    metadata["agent_session_id"] = session_id
                    run.run_metadata = metadata
            if event_type in {"usage", "step_completed"}:
                raw_usage = event.get("usage") if event_type == "usage" else event.get("tokens")
                normalised = _normalise_usage(raw_usage)
                for key, value in normalised.items():
                    provider_usage[key] = provider_usage.get(key, 0) + value
                cost = event.get("cost")
                if cost is None and isinstance(raw_usage, dict):
                    cost = raw_usage.get("cost")
                try:
                    cost_usd = float(cost) if cost is not None else None
                except (TypeError, ValueError):
                    cost_usd = None
                if cost_usd is not None:
                    provider_usage["cost"] = float(provider_usage.get("cost", 0.0)) + cost_usd
                    measurements["provider_cost_usd"] = float(provider_usage["cost"])
                metadata = dict(run.run_metadata or {})
                metadata["usage_totals"] = dict(provider_usage)
                run.run_metadata = metadata
                if event_type == "step_completed":
                    step = event.get("step")
                    if isinstance(step, int):
                        started = step_started_at.pop(step, None)
                        if started is None:
                            measurements["model_calls"] = int(measurements.get("model_calls", 0)) + 1
                        else:
                            step_tool_time = float(step_tool_time_ms.pop(step, 0.0))
                            measurements["model_time_ms"] = float(measurements.get("model_time_ms", 0.0)) + max(0.0, (event_now - started) * 1000.0 - step_tool_time)
                    if normalised:
                        record_run_telemetry(
                            db,
                            run,
                            kind="agent_step",
                            operation=f"{selected_backend}_turn",
                            status="completed",
                            input_tokens=normalised.get("input_tokens"),
                            output_tokens=normalised.get("output_tokens"),
                            total_tokens=normalised.get("total_tokens"),
                            cost_usd=cost_usd,
                            provider=agent_provider,
                            model=agent_model,
                            metadata={"step": event.get("step"), "provider_usage": raw_usage},
                        )
                if event_type == "usage":
                    persist_measurements()
                    run.heartbeat_at = datetime.now(timezone.utc)
                    run.updated_at = run.heartbeat_at
                    db.commit()
                    continue
            if event_type == "token":
                token = str(event.get("token") or "")
                output_chars += len(token)
                if token and measurements["time_to_first_output_ms"] is None:
                    measurements["time_to_first_output_ms"] = max(0.0, (event_now - turn_started) * 1000.0)
                if token:
                    assistant_text_parts.append(token)
                    token_buffer.append(token)
                    token_buffer_chars += len(token)
                    if token_buffer_chars >= _WORKSPACE_TOKEN_CHUNK_FLUSH_CHARS:
                        record_stream_event(
                            db,
                            run,
                            {"type": "token_chunk", "token": "".join(token_buffer)},
                        )
                        token_buffer.clear()
                        token_buffer_chars = 0
                        # Keep the conversational transcript available to a
                        # reconnecting workspace while the turn is still live.
                        persist_streamed_assistant(partial=True)
                        # The public workspace response replays committed run
                        # events from another database session. Token events
                        # are handled without the normal non-token commit below,
                        # so commit this bounded flush explicitly; otherwise
                        # live output stays invisible until a later tool/final event.
                        db.commit()
            if event_type == "approval_requested" and selected_backend == "codex":
                request_id = str(event.get("request_id") or "").strip()
                if not request_id:
                    raise RuntimeError("Codex returned an invalid approval request")
                metadata = dict(run.run_metadata or {})
                metadata["codex_pending_approval"] = {
                    "request_id": request_id,
                    "approval_kind": event.get("approval_kind"),
                    "reason": event.get("reason"),
                    "command": event.get("command"),
                    "cwd": event.get("cwd"),
                    "details": event.get("details"),
                }
                metadata.pop("codex_approval_response", None)
                run.run_metadata = metadata
                if run.status == "running":
                    transition_agent_run(
                        db,
                        run,
                        "waiting_tool",
                        event_type="run_waiting_for_approval",
                        payload={"request_id": request_id},
                    )
            elif event_type == "approval_resolved" and selected_backend == "codex":
                metadata = dict(run.run_metadata or {})
                metadata.pop("codex_pending_approval", None)
                metadata.pop("codex_approval_response", None)
                run.run_metadata = metadata
                if run.status == "waiting_tool":
                    transition_agent_run(
                        db,
                        run,
                        "running",
                        event_type="run_approval_resolved",
                        payload={
                            "request_id": event.get("request_id"),
                            "decision": event.get("decision"),
                        },
                    )
            if event_type == "question" and selected_backend == "codex":
                clarification = event.get("request")
                request_id = str(event.get("request_id") or "").strip()
                if not isinstance(clarification, dict) or not clarification.get("questions") or not request_id:
                    raise RuntimeError("Codex returned an invalid user-input request")
                agent_memory = dict(project.agent_memory or {})
                agent_memory["pending_clarifications"] = {
                    **clarification,
                    "runtime": "codex_app_server",
                    "request_id": request_id,
                }
                agent_memory["pending_clarifications_run_id"] = str(run.id)
                agent_memory["pre_clarification_project_status"] = str(project.status or "created")
                agent_memory["pre_clarification_agent_state"] = str(project.agent_state or "idle")
                project.agent_memory = agent_memory
                project.status = "needs_clarification"
                project.agent_state = "needs_clarification"
                metadata = dict(run.run_metadata or {})
                metadata["codex_pending_user_input"] = {
                    "request_id": request_id,
                    "request": clarification,
                }
                metadata.pop("codex_user_input_response", None)
                run.run_metadata = metadata
                if run.status == "running":
                    transition_agent_run(
                        db,
                        run,
                        "waiting_tool",
                        event_type="run_waiting_for_user_input",
                        payload={"request_id": request_id},
                    )
            elif event_type == "question_resolved" and selected_backend == "codex":
                agent_memory = dict(project.agent_memory or {})
                project.status = str(agent_memory.pop("pre_clarification_project_status", None) or "created")
                project.agent_state = str(agent_memory.pop("pre_clarification_agent_state", None) or "idle")
                agent_memory.pop("pending_clarifications", None)
                agent_memory.pop("pending_clarifications_run_id", None)
                project.agent_memory = agent_memory
                metadata = dict(run.run_metadata or {})
                metadata.pop("codex_pending_user_input", None)
                metadata.pop("codex_user_input_response", None)
                run.run_metadata = metadata
                if run.status == "waiting_tool":
                    transition_agent_run(
                        db,
                        run,
                        "running",
                        event_type="run_user_input_resolved",
                        payload={"request_id": event.get("request_id")},
                    )
            if event_type == "tool_started":
                tool_key = str(event.get("tool_call_id") or event.get("tool") or "unknown")
                if tool_key not in tool_started_at:
                    tool_started_at[tool_key] = event_now
                    tool_names[tool_key] = str(event.get("tool") or "workspace_tool")
                    tool_steps[tool_key] = event.get("step") if isinstance(event.get("step"), int) else None
                    measurements["tool_calls"] = int(measurements.get("tool_calls", 0)) + 1
                if run.status == "running":
                    transition_agent_run(db, run, "waiting_tool", event_type="tool_waiting", payload={"tool": event.get("tool")})
            elif event_type in {"tool_completed", "execution_queued", "action_event"} and run.status == "waiting_tool":
                transition_agent_run(db, run, "running", event_type="tool_resumed")
            elif event_type == "wait":
                waiting_for_dependency = True
                if dependency_wait_started is None:
                    dependency_wait_started = event_now
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
            if event["type"] in {"tool_completed", "action_event"} and not event.get("message_id"):
                tool_call_id, tool_name, tool_status, tool_summary = _tool_event_details(event)
                tool_message = record_run_message(
                    db,
                    project,
                    "tool",
                    tool_summary or f"{tool_name} completed",
                    kind="tool",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "status": tool_status,
                        "step": event.get("step"),
                        "action_event": event["type"] == "action_event",
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
                final_content = str(event.get("message") or "").strip()
                streamed_content = "".join(assistant_text_parts).strip()
                cancelled_with_output = bool(event.get("cancelled") and streamed_content)
                if streamed_content or assistant_message_id:
                    message = persist_streamed_assistant(
                        partial=cancelled_with_output,
                        content=streamed_content if cancelled_with_output else final_content or streamed_content,
                        extra_metadata=metadata,
                    )
                elif final_content:
                    message = record_run_message(
                        db,
                        project,
                        "assistant",
                        final_content,
                        metadata=metadata or None,
                    )
                    assistant_message_id = str(message.id)
                else:
                    message = None
                if message is not None:
                    event["message"] = message.content
                    if cancelled_with_output:
                        event["partial"] = True
                    event["message_id"] = str(message.id)


            if event["type"] == "final" and event.get("awaiting_answer"):
                clarification = event["awaiting_answer"]
                if not isinstance(clarification, dict) or not clarification.get("questions"):
                    raise RuntimeError("OpenCode returned an invalid clarification request")
                agent_memory = dict(project.agent_memory or {})
                agent_memory.pop("pending_question", None)
                agent_memory["pending_clarifications"] = clarification
                agent_memory["pending_clarifications_run_id"] = str(run.id)
                project.agent_memory = agent_memory

            if event.get("type") == "action_queued" and event.get("job_id"):
                append_continuation_step(
                    run,
                    action=str(event.get("action") or "workspace action"),
                    dependency_kind="job",
                    dependency_id=str(event["job_id"]),
                    instruction=str(user_message.content or data.message).strip(),
                    arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
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
            if event_type in {"tool_completed", "action_event"}:
                tool_key, tool_name, tool_status, tool_summary = _tool_event_details(event)
                tool_name = tool_names.pop(tool_key, tool_name)
                tool_step = tool_steps.pop(tool_key, None)
                started = tool_started_at.pop(tool_key, None)
                duration_ms = max(0.0, (event_now - started) * 1000.0) if started is not None else None
                if duration_ms is not None:
                    measurements["tool_time_ms"] = float(measurements.get("tool_time_ms", 0.0)) + duration_ms
                    if isinstance(tool_step, int):
                        step_tool_time_ms[tool_step] = float(step_tool_time_ms.get(tool_step, 0.0)) + duration_ms
                    if _is_render_or_compute_tool(tool_name, tool_summary):
                        measurements["render_time_ms"] = float(measurements.get("render_time_ms", 0.0)) + duration_ms
                record_run_telemetry(
                    db,
                    run,
                    kind="tool",
                    operation=tool_name,
                    status=tool_status,
                    duration_ms=duration_ms,
                    provider=agent_provider or settings.llm_provider,
                    model=agent_model or settings.llm_model,
                    error=tool_summary if tool_status in {"error", "failed"} else None,
                    metadata={"step": event.get("step"), "tool_call_id": tool_key, "event_type": event_type},
                )
            if event_type in {"final", "action_queued", "cancelled"}:
                terminal_metadata = dict(run.run_metadata or {})
                terminal_metadata.pop("codex_pending_approval", None)
                terminal_metadata.pop("codex_approval_response", None)
                run.run_metadata = terminal_metadata
                if terminal_metadata.get("codex_pending_user_input") and not event.get("awaiting_answer"):
                    agent_memory = dict(project.agent_memory or {})
                    project.status = str(agent_memory.pop("pre_clarification_project_status", None) or "created")
                    project.agent_state = str(agent_memory.pop("pre_clarification_agent_state", None) or "idle")
                    agent_memory.pop("pending_clarifications", None)
                    agent_memory.pop("pending_clarifications_run_id", None)
                    project.agent_memory = agent_memory
                    terminal_metadata.pop("codex_pending_user_input", None)
                    terminal_metadata.pop("codex_user_input_response", None)
                    run.run_metadata = terminal_metadata
                cancelled = event_type == "cancelled" or cancel_requested_now()
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
                waiting_for_answer = (
                    event_type == "final" and bool(event.get("awaiting_answer"))
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
                    and not waiting_for_answer
                )
                target_status = (
                    "cancelled"
                    if cancelled
                    else "paused"
                    if waiting_for_continuation or waiting_for_answer
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
                            else "run_waiting_for_answer"
                            if waiting_for_answer
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
                if waiting_for_answer:
                    sync_project_from_agent_run(
                        db,
                        run,
                        project,
                        summary="Waiting for your decision",
                        force=True,
                    )
                run.result_payload = {
                    "message_id": event.get("message_id"),
                    "event_type": event_type,
                    "ok": event.get("ok"),
                    "error": event.get("error"),
                    "awaiting_answer": event.get("awaiting_answer"),
                }
                # Close any provider operation that ended with cancellation or an
                # error before OpenCode emitted its normal completion part.
                for tool_key, started in list(tool_started_at.items()):
                    duration_ms = max(0.0, (event_now - started) * 1000.0)
                    measurements["tool_time_ms"] = float(measurements.get("tool_time_ms", 0.0)) + duration_ms
                    tool_step = tool_steps.pop(tool_key, None)
                    if isinstance(tool_step, int):
                        step_tool_time_ms[tool_step] = float(step_tool_time_ms.get(tool_step, 0.0)) + duration_ms
                    if _is_render_or_compute_tool(tool_names.pop(tool_key, "workspace_tool"), ""):
                        measurements["render_time_ms"] = float(measurements.get("render_time_ms", 0.0)) + duration_ms
                    tool_started_at.pop(tool_key, None)
                for step, started in list(step_started_at.items()):
                    step_tool_time = float(step_tool_time_ms.pop(step, 0.0))
                    measurements["model_time_ms"] = float(measurements.get("model_time_ms", 0.0)) + max(0.0, (event_now - started) * 1000.0 - step_tool_time)
                    step_started_at.pop(step, None)
                measurements["total_time_ms"] = max(0.0, (event_now - turn_started) * 1000.0)
                if dependency_wait_started is not None:
                    measurements["dependency_wait_ms"] = float(measurements.get("dependency_wait_ms", 0.0)) + max(0.0, (event_now - dependency_wait_started) * 1000.0)
                    dependency_wait_started = None
                persist_measurements()
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
                            if waiting_for_continuation or waiting_for_answer
                            else "failed"
                            if turn_failed
                            else "completed"
                        ),
                        duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                        provider=agent_provider or settings.llm_provider,
                        model=agent_model or settings.llm_model,
                        **telemetry_from_usage(
                            provider_usage,
                            fallback_input_tokens=approximate_tokens(data.message),
                            fallback_output_tokens=max(0, (output_chars + 3) // 4),
                            metadata={"chat_mode": data.chat_mode, "measurements": dict(measurements)},
                        ),
                    )
                    telemetry_written = True
            if event_type not in {"token", "reasoning_token"}:
                persist_measurements()
            run_metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
            if str(run_metadata.get("job_type") or "").strip().lower() in {"generate", "render", "edit"}:
                sync_project_from_agent_run(db, run, project)
            sync_compatibility_jobs(db, run)
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

    finally:
        if assistant_text_parts:
            try:
                persist_streamed_assistant(partial=True)
                db.commit()
            except Exception:
                logger.exception("Could not persist the streamed workspace response")
        close_stream = getattr(stream_source, "aclose", None)
        if close_stream is not None:
            await close_stream()

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
