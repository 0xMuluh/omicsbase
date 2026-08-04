"""Generic streaming agent loop shared by the workspace and note lenses.

One tool-calling loop, per-lens policy: an executor supplies the system
prompt, tools, conversation builder, live context, and every tool's
execution and event emission. The core owns the step accounting, token
streaming, retry guard, and tool-result feed-back.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from app.services.llm import stream_llm_with_tools

logger = logging.getLogger(__name__)

MAX_PERSISTED_TOOL_ARGUMENT_CHARS = 4000


def persistable_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep tool-call audit metadata bounded without storing tool results."""
    try:
        encoded = json.dumps(arguments, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return {"_unserializable": str(arguments)[:MAX_PERSISTED_TOOL_ARGUMENT_CHARS]}
    if len(encoded) <= MAX_PERSISTED_TOOL_ARGUMENT_CHARS:
        return arguments
    return {
        "_truncated": True,
        "preview": encoded[:MAX_PERSISTED_TOOL_ARGUMENT_CHARS],
    }


def tool_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        return json.dumps({"tool": tool_name, "arguments": arguments}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"{tool_name}:{arguments!r}"


@dataclass
class LegacyStepResult:
    """Outcome of the legacy JSON-decision LLM path, when a lens still uses it."""

    events: list[dict] = field(default_factory=list)
    finished: bool = False
    step_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ToolCallResult:
    """One tool call: the observation plus every event the lens emits for it.

    ``events`` are yielded in order before the generic trailing
    ``tool_completed``. ``end_turn`` ends the turn immediately (async
    actions, questions); ``final_event`` is yielded right before that.
    """

    observation: dict
    events: list[dict] = field(default_factory=list)
    summary: str | None = None
    end_turn: bool = False
    final_event: dict | None = None
    refresh_context: bool = False
    record_failure: bool = True
    emit_completed: bool = True


class AgentExecutor(Protocol):
    """Everything specific to one lens; the loop itself is lens-agnostic."""

    max_steps: int
    max_tokens: int
    max_tool_chars: int
    system_prompt: str
    tools: list[dict]
    use_retry_guard: bool
    cancelled_message: str
    default_final_message: str
    max_steps_message: str

    def initial_events(self, message: str) -> tuple[list[dict], bool]:
        """Pre-loop events (status, greetings). ``handled=True`` ends the turn."""
        ...

    def build_messages(self, message: str) -> list[dict]:
        """Persisted history plus the new user message."""
        ...

    def build_live_context(self) -> str:
        """Current live context; rebuilt when a tool refreshes it."""
        ...

    def fallback_events(self, exc: Exception) -> list[dict]:
        """Events to yield when the LLM call fails."""
        ...

    def final_event(self, message: str) -> dict:
        """Final event payload for the lens (memory_updates etc.)."""
        ...

    def max_steps_events(self) -> list[dict]:
        """Events to yield when the step limit is reached."""
        ...

    def tool_completed_event(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        status: str,
        summary: str,
        step: int,
    ) -> dict:
        ...

    def summary_for(self, tool_name: str, observation: dict) -> str:
        ...

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        step: int,
        tool_call_id: str,
        persisted_arguments: dict[str, Any],
        step_text: str,
    ) -> ToolCallResult:
        ...

    async def legacy_llm_step(self, messages: list[dict], *, step: int) -> LegacyStepResult | None:
        """Legacy JSON-decision path; return None to use native tool calling."""
        ...

    def use_fast_path(self, message: str) -> bool:
        """Whether this turn is a simple question answered without tools."""
        return False

    async def fast_path_events(self, message: str) -> AsyncIterator[dict]:
        """Stream the direct answer events when use_fast_path returned True."""
        if False:
            yield {}
        ...


async def run_agent_loop(
    executor: AgentExecutor,
    message: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict]:
    """Run one streaming agent turn with native LLM function calling.

    Text tokens stream directly to the client. Tool calls are executed
    and fed back to the model for the next iteration.
    """
    initial_events, handled = executor.initial_events(message)
    for event in initial_events:
        yield event
    if handled:
        return

    if executor.use_fast_path(message):
        async for event in executor.fast_path_events(message):
            yield event
        return

    messages = executor.build_messages(message)
    live_context = executor.build_live_context()
    collected_text = ""
    failed_tool_calls: dict[str, str] = {}

    for step in range(1, executor.max_steps + 1):
        if cancel_check and cancel_check():
            yield {"type": "cancelled", "message": executor.cancelled_message}
            return
        tool_calls_this_step: list[dict[str, Any]] = []
        step_text = ""

        try:
            legacy = await executor.legacy_llm_step(messages, step=step)
            if legacy is not None:
                for event in legacy.events:
                    yield event
                if legacy.finished:
                    return
                step_text = legacy.step_text
                tool_calls_this_step = legacy.tool_calls
            else:
                async for event in stream_llm_with_tools(
                    system_prompt=executor.system_prompt,
                    messages=messages,
                    tools=executor.tools,
                    max_tokens=executor.max_tokens,
                    live_context=live_context,
                ):
                    if event["type"] == "usage":
                        yield {"type": "usage", "usage": event.get("usage") or {}}
                    elif event["type"] == "text_delta":
                        token = event["content"]
                        step_text += token
                        collected_text += token
                        yield {"type": "token", "token": token}
                    elif event["type"] == "tool_call":
                        tool_calls_this_step.append(event)
        except Exception as exc:
            logger.exception("Streaming agent LLM call failed: %s", exc)
            for event in executor.fallback_events(exc):
                yield event
            return

        # If model produced only text (no tool calls), it's the final answer
        if not tool_calls_this_step:
            final_text = collected_text.strip() or executor.default_final_message
            yield executor.final_event(final_text)
            return

        # Build assistant message with tool_calls for conversation history
        assistant_tool_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in tool_calls_this_step
        ]
        messages.append({
            "role": "assistant",
            "content": step_text or None,
            "tool_calls": assistant_tool_calls,
        })

        # Execute each tool call
        for tc in tool_calls_this_step:
            tool_name = tc["name"]
            arguments = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
            persisted_arguments = persistable_tool_arguments(arguments)
            signature = tool_signature(tool_name, arguments)
            if executor.use_retry_guard and signature in failed_tool_calls:
                blocker = failed_tool_calls[signature]
                message = (
                    f"I already tried {tool_name} with those arguments and it failed, so I stopped retrying. "
                    f"The exact blocker was: {blocker}"
                )
                yield {"type": "token", "token": message}
                yield executor.final_event(message)
                return

            result = await executor.execute_tool(
                tool_name,
                arguments,
                step=step,
                tool_call_id=tc["id"],
                persisted_arguments=persisted_arguments,
                step_text=step_text,
            )
            for event in result.events:
                yield event
            if result.end_turn:
                if result.final_event is not None:
                    yield result.final_event
                return

            if result.observation.get("status") == "error" and result.record_failure:
                failed_tool_calls[signature] = str(result.observation.get("error") or "unknown error")[:2000]
            status = "ok" if result.observation.get("status") != "error" else "error"
            summary = result.summary or executor.summary_for(tool_name, result.observation)
            if result.emit_completed:
                yield executor.tool_completed_event(
                    tool_name,
                    tc["id"],
                    persisted_arguments,
                    status,
                    summary,
                    step,
                )

            # Feed tool result back to the model
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result.observation, default=str)[:executor.max_tool_chars],
            })

            if result.refresh_context:
                live_context = executor.build_live_context()

        # Continue to next step (model will see tool results)
        collected_text = ""  # Reset for next streaming round

    # Reached max steps
    for event in executor.max_steps_events():
        yield event
