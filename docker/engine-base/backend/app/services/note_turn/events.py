"""Durable NoteThread event streaming and run telemetry."""

from __future__ import annotations

import asyncio
import json
import logging

from app.services.note_payloads import cell_payload as _cell_payload
from app.services.note_store import get_tenant_thread as _get_note_thread_for_tenant, thread_summary_payload as _thread_summary_payload
from app.config import settings
from app.services.agent_plans import append_continuation_step, get_continuation_plan, mark_continuation_consumed
from app.services.agent_runs import (
    TOKEN_CHUNK_FLUSH_CHARS,
    approximate_tokens,
    record_run_telemetry,
    record_stream_event,
    run_cancel_requested,
    serialize_agent_run,
    telemetry_from_usage,
    transition_agent_run,
)
from app.services.note_agent import append_note_cell, stream_note_agent

logger = logging.getLogger(__name__)


def _normalise_usage(usage) -> dict[str, int]:
    """Map provider usage aliases to one canonical set of counters."""
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "input", "prompt_tokens", "prompt"),
        "output_tokens": ("output_tokens", "output", "completion_tokens", "completion"),
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


async def stream_turn_events(
    *,
    db,
    run,
    thread_id: str,
    tenant_id: str,
    turn_id: str,
    message: str,
    data,
    agent_message: str,
    user_cell,
    active_thread,
    prior_cells,
    context,
    continuation_resume: bool,
    action_handler,
    knowledge_search_handler,
    tool_dispatcher,
    worker_session_factory,
    demonstration_request: bool = False,
):
    final_written = False
    turn_started = asyncio.get_running_loop().time()
    output_chars = 0
    provider_usage: dict[str, int] = {}
    tool_started_at = {}
    token_buffer: list[str] = []
    telemetry_written = False
    pending_async_execution = False
    waiting_for_dependency = False

    def cancel_requested_now() -> bool:
        check_db = worker_session_factory()
        try:
            return run_cancel_requested(check_db, str(run.id))
        finally:
            check_db.close()
    record_stream_event(db, run, {"type": "note_cell", "role": "user", "turn_id": turn_id, "cell": _cell_payload(user_cell)})
    db.commit()
    yield json.dumps({"type": "run", "run": serialize_agent_run(run)}, default=str) + "\n"
    yield json.dumps(
        {"type": "note_cell", "role": "user", "turn_id": turn_id, "cell": _cell_payload(user_cell)},
    default=str) + "\n"
    yield json.dumps(
        {"type": "thread_updated", "turn_id": turn_id, "thread": _thread_summary_payload(active_thread)},
    default=str) + "\n"
    stream_source = stream_note_agent(
        message=agent_message,
        cells=prior_cells,
        context=context,
        action_handler=action_handler,
        knowledge_search_handler=knowledge_search_handler,
        cancel_check=cancel_requested_now,
    )
    try:
        async for event in stream_source:
            output_event = dict(event)
            output_event["turn_id"] = turn_id
            event_type = str(output_event.get("type") or "stream_event")
            if event_type == "usage":
                for key, value in _normalise_usage(output_event.get("usage")).items():
                    provider_usage[key] = provider_usage.get(key, 0) + value
                continue
            if event_type == "token":
                token = str(output_event.get("token") or "")
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
                tool_started_at[str(output_event.get("tool_call_id") or output_event.get("tool") or "unknown")] = asyncio.get_running_loop().time()
                if run.status == "running":
                    transition_agent_run(db, run, "waiting_tool", event_type="tool_waiting", payload={"tool": output_event.get("tool")})
            elif event_type in {"tool_completed", "execution_queued"} and run.status == "waiting_tool":
                transition_agent_run(db, run, "running", event_type="tool_resumed")
            elif event_type == "wait":
                waiting_for_dependency = True
                if run.status in {"running", "waiting_tool"}:
                    transition_agent_run(
                        db,
                        run,
                        "paused",
                        event_type="run_waiting",
                        payload={"dependency": output_event.get("dependency"), "step": output_event.get("step")},
                    )
            if event_type == "final":
                final_text = str(output_event.get("message") or "").strip()
                if final_text:
                    final_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
                    assistant_cell = append_note_cell(
                        db,
                        final_thread,
                        cell_type="markdown",
                        content=final_text,
                        metadata={
                            "turn_id": turn_id,
                            "role": "assistant",
                            "generated_by": "note_agent",
                            "knowledge_sources": tool_dispatcher.knowledge_sources[-12:],
                        },
                        created_by="agent",
                    )
                    output_event["role"] = "assistant"
                    output_event["cell"] = _cell_payload(assistant_cell)
                    final_written = True
            if output_event.get("type") == "execution_queued" and isinstance(output_event.get("execution"), dict):
                pending_async_execution = True
                execution = output_event["execution"]
                execution_id = execution.get("id")
                if execution_id:
                    append_continuation_step(
                        run,
                        action="run_r_cell",
                        dependency_kind="execution",
                        dependency_id=str(execution_id),
                        instruction=str(message),
                        arguments=output_event.get("tool_arguments") if isinstance(output_event.get("tool_arguments"), dict) else {},
                        dependency_status=str(execution.get("status") or "queued"),
                    )

            continuation = get_continuation_plan(run)
            if (
                output_event.get("type") == "final"
                and pending_async_execution
                and not continuation_resume
                and continuation
                and (
                    continuation.get("status") == "waiting"
                    or (
                        not settings.note_execution_agent_wait_enabled
                        and continuation.get("status") in {"ready", "failed"}
                    )
                )
            ):
                output_event["continuation_pending"] = True

            event_type = str(output_event.get("type") or event_type)
            if event_type in {"final", "cancelled"} and token_buffer:
                record_stream_event(
                    db,
                    run,
                    {"type": "token_chunk", "token": "".join(token_buffer)},
                )
                token_buffer.clear()
            if event_type != "token":
                replay_event = record_stream_event(db, run, output_event)
                output_event["run_id"] = str(run.id)
                output_event["run_sequence"] = replay_event.sequence
            if event_type == "tool_completed":
                tool_key = str(output_event.get("tool_call_id") or output_event.get("tool") or "unknown")
                started = tool_started_at.pop(tool_key, None)
                record_run_telemetry(
                    db,
                    run,
                    kind="tool",
                    operation=str(output_event.get("tool") or "note_tool"),
                    status=str(output_event.get("status") or "completed"),
                    duration_ms=((asyncio.get_running_loop().time() - started) * 1000) if started else None,
                    provider=settings.llm_provider,
                    model=settings.llm_model,
                    error=output_event.get("summary") if output_event.get("status") == "error" else None,
                    metadata={"step": output_event.get("step"), "tool_call_id": output_event.get("tool_call_id")},
                )
            if event_type in {"final", "cancelled"}:
                cancelled = event_type == "cancelled" or cancel_requested_now()
                continuation = get_continuation_plan(run)
                waiting_for_continuation = (
                    waiting_for_dependency
                    or (
                        event_type == "final"
                        and bool(continuation)
                        and continuation.get("status") == "waiting"
                    )
                )
                if (
                    event_type == "final"
                    and pending_async_execution
                    and not continuation_resume
                    and not settings.note_execution_agent_wait_enabled
                    and bool(continuation)
                    and continuation.get("status") in {"ready", "failed"}
                ):
                    waiting_for_continuation = True
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
                if (
                    event_type == "final"
                    and not cancelled
                    and not waiting_for_continuation
                    and demonstration_request
                    and not tool_dispatcher.knowledge_sources
                    and tool_dispatcher.generated_code_cells == 0
                    and tool_dispatcher.generated_note_cells == 0
                ):
                    logger.warning(
                        "note_demonstration_completed_without_grounding",
                        extra={
                            "turn_id": turn_id,
                            "thread_id": thread_id,
                            "knowledge_sources": 0,
                            "generated_code_cells": tool_dispatcher.generated_code_cells,
                            "generated_note_cells": tool_dispatcher.generated_note_cells,
                        },
                    )
                target_status = "cancelled" if cancelled else ("paused" if waiting_for_continuation else "completed")
                if run.status not in {"completed", "failed", "cancelled"}:
                    transition_agent_run(
                        db,
                        run,
                        target_status,
                        event_type=(
                            "run_cancelled"
                            if cancelled
                            else "run_waiting_continuation"
                            if waiting_for_continuation
                            else "run_completed"
                        ),
                        payload={"cell_id": str((output_event.get("cell") or {}).get("id")) if isinstance(output_event.get("cell"), dict) else None},
                    )
                run.result_payload = {"cell_id": (output_event.get("cell") or {}).get("id") if isinstance(output_event.get("cell"), dict) else None, "event_type": event_type}
                if not telemetry_written:
                    record_run_telemetry(
                        db,
                        run,
                        kind="agent",
                        operation="note_turn",
                        status=(
                            "cancelled"
                            if cancelled
                            else "paused"
                            if waiting_for_continuation
                            else "completed"
                        ),
                        duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                        provider=settings.llm_provider,
                        model=settings.llm_model,
                        **telemetry_from_usage(
                            provider_usage,
                            fallback_input_tokens=approximate_tokens(message),
                            fallback_output_tokens=max(0, (output_chars + 3) // 4),
                            metadata={"auto_execute": data.auto_execute},
                        ),
                    )
                    telemetry_written = True
            db.commit()
            yield json.dumps(output_event, default=str) + "\n"
    except asyncio.CancelledError:
        try:
            if run.status not in {"completed", "failed", "cancelled"}:
                cancelled = cancel_requested_now()
                transition_agent_run(
                    db,
                    run,
                    "cancelled" if cancelled else "paused",
                    event_type="run_cancelled" if cancelled else "run_paused",
                    payload={"reason": "stream disconnected"},
                )
                if not telemetry_written:
                    record_run_telemetry(
                        db,
                        run,
                        kind="agent",
                        operation="note_turn",
                        status="cancelled" if cancelled else "paused",
                        duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                        input_tokens=approximate_tokens(message),
                        output_tokens=max(0, (output_chars + 3) // 4),
                        provider=settings.llm_provider,
                        model=settings.llm_model,
                        metadata={"estimated_tokens": True, "disconnect": True},
                    )
                db.commit()
        finally:
            raise
    except Exception as exc:
        logger.exception("NoteThread turn failed: %s", exc)
        try:
            if run.status not in {"completed", "failed", "cancelled"}:
                transition_agent_run(db, run, "failed", event_type="run_failed", payload={"error": str(exc)[:1000]})
                if not telemetry_written:
                    record_run_telemetry(
                        db,
                        run,
                        kind="agent",
                        operation="note_turn",
                        status="failed",
                        duration_ms=(asyncio.get_running_loop().time() - turn_started) * 1000,
                        input_tokens=approximate_tokens(message),
                        output_tokens=max(0, (output_chars + 3) // 4),
                        provider=settings.llm_provider,
                        model=settings.llm_model,
                        error=str(exc),
                        metadata={"estimated_tokens": True},
                    )
                db.commit()
        except Exception:
            db.rollback()
        fallback = (
            "I preserved your question in this notebook, but I could not complete the agent turn. "
            "No unrequested computation was run. You can retry or continue from the saved cells."
        )
        if not final_written:
            try:
                final_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
                assistant_cell = append_note_cell(
                    db,
                    final_thread,
                    cell_type="markdown",
                    content=fallback,
                    metadata={"turn_id": turn_id, "role": "assistant", "generated_by": "note_agent"},
                    created_by="agent",
                )
                final_written = True
                yield json.dumps(
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "message": fallback,
                    },
                default=str) + "\n"
                yield json.dumps(
                    {
                        "type": "final",
                        "turn_id": turn_id,
                        "role": "assistant",
                        "message": fallback,
                        "cell": _cell_payload(assistant_cell),
                    },
                default=str) + "\n"
            except Exception:
                logger.exception("Could not persist NoteThread fallback cell")
    finally:
        close_stream = getattr(stream_source, "aclose", None)
        if close_stream is not None:
            await close_stream()
