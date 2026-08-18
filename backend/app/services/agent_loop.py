"""Native OmicsBase agent loop: one model, one conversation, tools as actuators.

The loop owns the whole run. It streams model text to the client, executes the
model's tool calls, and feeds bounded observations back into the same
conversation so the model can inspect, decide, repair, and finish without any
stage handoff. Determinism belongs below the loop (tool executors, budgets,
journals); nothing above the model decides, routes, or vetoes.

The engine consumes the same executor contract the OpenHands SDK adapter
used (``build_messages``, ``execute_tool``, ``initial_events``, ``final_event``,
``fallback_events``, ``tool_completed_event``), so lens executors plug in
unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.services.agent_core import (
    ToolCallResult,
    TurnBudget,
    persistable_tool_arguments,
    tool_signature,
)
from app.services.context_budget import bounded_json

logger = logging.getLogger(__name__)

MAX_TOOL_RETRIES = 2

_DEFAULT_RATE_LIMIT_WAIT = 30.0
_MAX_RATE_LIMIT_WAIT = 90.0


def _suggested_retry_delay(exc: Exception, attempt: int) -> float:
    """Wait time before retrying a rate-limited step.

    Providers often include a hint ("Please retry in 31.7s" / RetryInfo); use
    it when present, otherwise escalate a conservative default per attempt.
    """
    import re

    for candidate in (exc, getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        match = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", str(candidate), re.IGNORECASE)
        if match:
            try:
                return min(_MAX_RATE_LIMIT_WAIT, float(match.group(1)) + 2.0)
            except ValueError:
                pass
    return min(_MAX_RATE_LIMIT_WAIT, _DEFAULT_RATE_LIMIT_WAIT * attempt)


async def _retry_rate_limited(
    stream_factory: Callable[[], AsyncIterator[dict[str, Any]]],
    *,
    max_waits: int,
    cancel_check: Callable[[], bool] | None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield from the stream, waiting out per-minute rate limits.

    Only retries when the failed attempt produced no events, so no output is
    ever duplicated; anything else re-raises unchanged.
    """
    from app.services.provider_errors import LLMRateLimitError

    waits = 0
    while True:
        received_any = False
        try:
            async for event in stream_factory():
                received_any = True
                yield event
            return
        except LLMRateLimitError as exc:
            waits += 1
            if received_any or waits > max_waits:
                raise
            if cancel_check is not None and cancel_check():
                raise
            delay = _suggested_retry_delay(exc, waits)
            logger.warning(
                "Provider rate-limited; waiting %.0fs before retrying the step (%d/%d)",
                delay, waits, max_waits,
            )
            await asyncio.sleep(delay)

_STATUS_VALUES = {"error", "duplicate", "unsupported", "budget_exhausted", "ok"}
_PRIORITY_KEYS = ("status", "summary", "error", "path", "revision", "sha256", "data", "truncated")


def _system_prompt(executor: Any) -> str:
    try:
        context = str(executor.build_live_context() or "")
    except Exception:
        context = ""
    boundary = (
        "## OmicsBase runtime boundary\n"
        "You orchestrate OmicsBase tool calls to complete the user's request. "
        "OmicsBase enforces permissions, configured safety limits, data protection, and persistence. "
        "When a tool call fails, read its observation, repair the root cause, and try again or "
        "choose a different approach. "
        "Use only the listed OmicsBase tools; never use shell, filesystem, browser, "
        "or an unlisted capability. Finish with a concise summary."
    )
    limit = max(4_000, int(getattr(executor, "system_context_chars", 80_000) or 80_000))
    return (
        str(getattr(executor, "system_prompt", ""))
        + "\n\n"
        + boundary
        + "\n\n## Current OmicsBase context\n"
        + context[:limit]
    )


def _tool_call_message(tool_calls: list[dict[str, Any]], text: str) -> dict[str, Any]:
    entries = []
    for call in tool_calls:
        entry: dict[str, Any] = {
            "id": call["id"],
            "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"]},
        }
        # Gemini 3 requires the thought_signature to be echoed back with the
        # function call it signed; other providers ignore or strip it.
        if call.get("thought_signature"):
            entry["thought_signature"] = call["thought_signature"]
        entries.append(entry)
    return {"role": "assistant", "content": text or None, "tool_calls": entries}


def _observation_content(observation: dict[str, Any], executor: Any) -> str:
    max_chars = int(getattr(executor, "max_tool_chars", 40_000) or 40_000)
    return bounded_json(observation, max_chars, priority_keys=_PRIORITY_KEYS)


class _RunState:
    """Per-turn accounting shared by the loop and its tool executions."""

    def __init__(self, executor: Any):
        self.executor = executor
        self.budget = TurnBudget.from_settings(
            profile=str(getattr(executor, "budget_profile", "agent") or "agent")
        )
        self.max_tool_retries = max(
            0,
            int(getattr(executor, "max_tool_retries", MAX_TOOL_RETRIES) or MAX_TOOL_RETRIES),
        )
        self.step = 0
        self.text = ""
        self.max_stream_text_chars = max(
            1_000,
            int(getattr(executor, "max_stream_text_chars", 100_000) or 100_000),
        )
        self.final_emitted = False
        self.cancelled = False
        self.budget_error: str | None = None
        self.wait_for: dict[str, Any] | None = None
        self.seen_non_idempotent: set[str] = set()
        self.failed_tool_calls: dict[str, dict[str, str]] = {}
        self.retry_counts: dict[str, int] = {}
        self.responses = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.exception: Exception | None = None
        # Consecutive failures per tool name; any success of that tool resets.
        # Guards against the livelock where a model retries one failing tool
        # with slightly different arguments forever, dodging the retry guard.
        self.consecutive_failures: dict[str, int] = {}

    def note_tool_outcome(self, name: str, failed: bool) -> str | None:
        """Record the outcome; return a breaker message when the tool is stuck."""
        limit = 6
        if not failed:
            self.consecutive_failures.pop(name, None)
            return None
        count = self.consecutive_failures.get(name, 0) + 1
        self.consecutive_failures[name] = count
        if count >= limit:
            return (
                f"{name} failed {count} times in a row with different arguments. "
                "Stopping this turn so the failure can be inspected instead of "
                "burning the remaining step budget on repeats."
            )
        return None

    def append_text(self, value: str) -> None:
        self.text = (self.text + value)[-self.max_stream_text_chars:]

    def record_usage(self, usage: dict[str, Any]) -> None:
        self.responses += 1
        allowed, reason = self.budget.record_usage(usage)
        try:
            self.input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            pass
        if not allowed:
            self.budget_error = reason or "agent token budget exhausted"

    def usage_summary(self) -> dict[str, Any]:
        return {
            "responses": self.responses,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens or self.input_tokens + self.output_tokens,
        }


async def _execute_call(state: _RunState, call: dict[str, Any], step: int) -> ToolCallResult:
    """Run one tool call under budget, duplicate, and retry guards."""
    executor = state.executor
    name = str(call["name"])
    arguments = call.get("arguments") or {}
    spec = None
    try:
        spec = executor.tool_spec(name)
    except Exception:
        spec = None
    idempotency = str(getattr(spec, "idempotency", "read_only") or "read_only")
    signature = tool_signature(name, arguments)

    # Identical failed calls are bounded by count alone: the model sees every
    # error observation and can change its approach; this only stops loops.
    # Argument-less tools (render_report) are exempt: between two calls the
    # workspace itself changed, so a repeat is a new attempt, not a loop.
    failure = state.failed_tool_calls.get(signature)
    retry_count = state.retry_counts.get(signature, 0)
    retry_blocked = bool(
        failure
        and arguments
        and getattr(executor, "use_retry_guard", False)
        and retry_count >= state.max_tool_retries
    )
    if failure and arguments and getattr(executor, "use_retry_guard", False) and not retry_blocked:
        state.retry_counts[signature] = retry_count + 1

    if retry_blocked:
        message = (
            f"Retry limit ({state.max_tool_retries}) reached for {name} with those arguments. "
            f"The exact blocker was: {failure.get('message', 'unknown failure')}"
        )
        return ToolCallResult(
            observation={
                "status": "error",
                "error": message,
                "retry_count": retry_count,
                "retry_limit": state.max_tool_retries,
            },
            summary=message,
            record_failure=False,
        )
    if idempotency == "non_idempotent" and signature in state.seen_non_idempotent:
        return ToolCallResult(
            observation={
                "status": "duplicate",
                "error": (
                    "Duplicate non-idempotent tool call blocked: an identical call already "
                    "succeeded this turn. Change the arguments or skip the repeat."
                ),
            },
            summary="Duplicate non-idempotent tool call blocked",
            record_failure=False,
        )

    allowed, reason = state.budget.try_consume_tool(
        cost=int(getattr(spec, "effective_budget", 1) or 1),
        mutating=str(getattr(spec, "risk", "read")) in {"write", "execute"},
    )
    if not allowed:
        return ToolCallResult(
            observation={"status": "budget_exhausted", "error": reason or "Tool budget exhausted."},
            summary=reason or "Tool budget exhausted",
            record_failure=False,
            end_turn=True,
            final_event=executor.final_event(reason or "Tool budget exhausted."),
        )
    if idempotency == "non_idempotent":
        # Record the signature only after a SUCCESSFUL execution. Retrying a
        # failed render/script with fixed source is the intended repair loop;
        # only an identical repeat of an already-successful mutation is a slip.
        try:
            result = await executor.execute_tool(
                name,
                arguments,
                step=step,
                tool_call_id=str(call.get("id") or ""),
                persisted_arguments=persistable_tool_arguments(arguments),
                step_text=str(call.get("step_text") or ""),
            )
        except Exception as exc:
            logger.exception("Agent tool %s failed", name)
            return ToolCallResult(
                observation={"status": "error", "error": str(exc)},
                summary=str(exc)[:500],
            )
        if not _failed(result.observation if isinstance(result.observation, dict) else {}):
            state.seen_non_idempotent.add(signature)
        return result

    try:
        return await executor.execute_tool(
            name,
            arguments,
            step=step,
            tool_call_id=str(call.get("id") or ""),
            persisted_arguments=persistable_tool_arguments(arguments),
            step_text=str(call.get("step_text") or ""),
        )
    except Exception as exc:
        logger.exception("Agent tool %s failed", name)
        return ToolCallResult(
            observation={"status": "error", "error": str(exc)},
            summary=str(exc)[:500],
        )


def _failed(observation: dict[str, Any]) -> bool:
    if str(observation.get("status") or "").lower() == "error":
        return True
    execution = observation.get("execution")
    if isinstance(execution, dict):
        return str(execution.get("status") or "").lower() in {
            "failed", "timed_out", "cancelled", "completed_with_errors",
        }
    return False


async def _run_batch(
    state: _RunState,
    calls: list[dict[str, Any]],
    step: int,
    queue: asyncio.Queue[dict[str, Any]],
) -> list[ToolCallResult]:
    """Execute one batch of tool calls, running parallel-eligible calls together."""
    if len(calls) > 1 and all(bool(state.executor.parallel_eligible(c["name"])) for c in calls):
        for call in calls:
            queue.put_nowait({
                "type": "tool_started",
                "tool": call["name"],
                "tool_call_id": call.get("id"),
                "arguments": persistable_tool_arguments(call.get("arguments") or {}),
                "reason": str(call.get("step_text") or call["name"]),
                "step": step,
            })
        results = await asyncio.gather(*(_execute_call(state, call, step) for call in calls))
        return list(results)
    results: list[ToolCallResult] = []
    for call in calls:
        queue.put_nowait({
            "type": "tool_started",
            "tool": call["name"],
            "tool_call_id": call.get("id"),
            "arguments": persistable_tool_arguments(call.get("arguments") or {}),
            "reason": str(call.get("step_text") or call["name"]),
            "step": step,
        })
        results.append(await _execute_call(state, call, step))
    return results


def _result_payload(state: _RunState, result: ToolCallResult) -> dict[str, Any]:
    value = result.observation if isinstance(result.observation, dict) else {"result": result.observation}
    value = dict(value)
    if result.refresh_context:
        try:
            value["_omicsbase_live_context"] = str(state.executor.build_live_context())[:40_000]
        except Exception:
            logger.warning("Agent context refresh failed", exc_info=True)
    return value


async def run_native_agent(
    executor: Any,
    message: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one complete agent turn: stream, act, observe, repeat, finish."""
    from app.services.llm import stream_llm_with_tools

    initial, handled = executor.initial_events(message)
    for event in initial:
        yield event
    if handled:
        return

    state = _RunState(executor)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    messages = executor.build_messages(message)
    system = _system_prompt(executor)
    provider_override = getattr(executor, "llm_provider_override", None)
    model_override = getattr(executor, "llm_model_override", None)
    max_tokens = int(getattr(executor, "max_tokens", 16_000) or 16_000)
    max_steps = max(1, int(getattr(executor, "max_steps", 6) or 6))
    emit_tokens = bool(getattr(executor, "emit_tokens", True))
    tools = list(getattr(executor, "tools", []) or [])
    step_text = ""

    async def run_turn() -> None:
        try:
            for _step in range(max_steps):
                if cancel_check is not None and cancel_check():
                    state.cancelled = True
                    return
                allowed, reason = state.budget.try_record_llm_call()
                if not allowed:
                    state.budget_error = reason or "LLM call budget exhausted"
                    return

                text = ""
                calls: list[dict[str, Any]] = []

                async def _stream_step():
                    async for event in stream_llm_with_tools(
                        system_prompt=system,
                        messages=messages,
                        tools=tools,
                        max_tokens=max_tokens,
                        live_context=None,
                        model_override=model_override,
                        provider_override=provider_override,
                    ):
                        yield event

                # Per-minute token quotas (free tiers especially) reject whole
                # requests with a 429 before any output; waiting out the window
                # and retrying the step is safe because nothing was consumed.
                async for event in _retry_rate_limited(
                    _stream_step,
                    max_waits=3,
                    cancel_check=cancel_check,
                ):
                    kind = event.get("type")
                    if kind == "text_delta":
                        chunk = str(event.get("content") or "")
                        if not chunk:
                            continue
                        text += chunk
                        state.append_text(chunk)
                        if not state.budget.record_generated(chunk):
                            state.budget_error = (
                                f"the run allows at most {state.budget.max_generated_tokens} generated tokens"
                            )
                            return
                        if emit_tokens:
                            queue.put_nowait({"type": "token", "token": chunk})
                    elif kind == "tool_call":
                        calls.append({
                            "id": event.get("id") or f"call-{state.step}-{len(calls)}",
                            "name": str(event.get("name") or ""),
                            "arguments": event.get("arguments") or {},
                            "step_text": step_text,
                            "thought_signature": event.get("thought_signature"),
                        })
                    elif kind == "usage":
                        usage = event.get("usage")
                        if isinstance(usage, dict) and usage:
                            state.record_usage(usage)
                            queue.put_nowait({"type": "usage", "usage": usage})
                            if state.budget_error:
                                return

                if state.budget_error or state.cancelled:
                    return

                if not calls:
                    final = text.strip() or str(executor.default_final_message or "")
                    state.final_emitted = True
                    queue.put_nowait(executor.final_event(final))
                    return

                state.step += 1
                step = state.step
                if text:
                    # Keep any interleaved reasoning text in the conversation.
                    messages.append({"role": "assistant", "content": text})
                messages.append(_tool_call_message(calls, text))

                results = await _run_batch(state, calls, step, queue)
                for call, result in zip(calls, results):
                    payload = _result_payload(state, result)
                    failed = _failed(payload)
                    if failed and result.record_failure:
                        state.failed_tool_calls.setdefault(
                            tool_signature(call["name"], call.get("arguments") or {}),
                            {
                                "message": str(payload.get("error") or payload.get("execution") or "unknown error")[:2000],
                            },
                        )
                    status = "error" if failed else str(payload.get("status") or "ok")
                    if status not in _STATUS_VALUES:
                        status = "ok"
                    summary = result.summary
                    if not summary:
                        try:
                            summary = executor.summary_for(call["name"], payload)
                        except Exception:
                            summary = call["name"]
                    for emitted in result.events:
                        if isinstance(emitted, dict) and emitted.get("type") != "tool_started":
                            queue.put_nowait(emitted)
                    if result.emit_completed:
                        queue.put_nowait(executor.tool_completed_event(
                            call["name"],
                            str(call.get("id") or ""),
                            persistable_tool_arguments(call.get("arguments") or {}),
                            status,
                            str(summary),
                            step,
                        ))
                    retained = _observation_content(payload, executor)
                    retrieved_limit = state.budget.max_retrieved_chars
                    if retrieved_limit is not None and len(retained) > retrieved_limit:
                        retained = retained[:retrieved_limit]
                    if not state.budget.record_retrieved(retained):
                        state.budget_error = (
                            "Run budget exhausted before another tool observation could be retained."
                        )
                        return
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "content": retained,
                    })
                    breaker = state.note_tool_outcome(str(call["name"]), failed)
                    if breaker is not None and not state.final_emitted:
                        state.final_emitted = True
                        queue.put_nowait(executor.final_event(breaker))
                        return
                    if result.wait_for:
                        state.wait_for = result.wait_for
                        queue.put_nowait({"type": "wait", "dependency": result.wait_for, "step": step})
                        if result.final_event is not None:
                            state.final_emitted = True
                            queue.put_nowait(result.final_event)
                        return
                    if result.end_turn:
                        if result.final_event is not None:
                            state.final_emitted = True
                            queue.put_nowait(result.final_event)
                        return

                if cancel_check is not None and cancel_check():
                    state.cancelled = True
                    return
            # Step ceiling reached without a final answer.
            final = state.text.strip() or (
                f"Stopped after {max_steps} steps without a final summary; "
                "raise the agent step budget to continue."
            )
            state.final_emitted = True
            queue.put_nowait(executor.final_event(final))
        except Exception as exc:
            logger.exception("Agent loop failed")
            state.exception = exc
        finally:
            queue.put_nowait({"type": "__done__"})

    turn_task = asyncio.create_task(run_turn())
    try:
        while True:
            event = await queue.get()
            if event.get("type") == "__done__":
                break
            yield event
        await turn_task
    finally:
        if not turn_task.done():
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)

    yield {"type": "budget", "budget": state.budget.snapshot()}
    yield {"type": "usage_summary", "usage": state.usage_summary()}

    exc = getattr(state, "exception", None)
    if exc is not None:
        for event in executor.fallback_events(exc):
            yield event
        return
    if state.cancelled:
        yield {"type": "cancelled", "message": executor.cancelled_message}
        return
    if state.budget_error and not state.final_emitted:
        final = executor.final_event(f"Run budget exhausted: {state.budget_error}.")
        if isinstance(final, dict):
            final["budget"] = state.budget.snapshot()
        yield final
        return
    if not state.final_emitted and not state.wait_for:
        yield executor.final_event(
            state.text.strip() or executor.default_final_message
        )


async def stream_agent_turn(
    executor: Any,
    message: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Public entry: run one agent turn on the native loop."""
    async for event in run_native_agent(executor, message, cancel_check=cancel_check):
        yield event


__all__ = ["run_native_agent", "stream_agent_turn"]
