"""Generic streaming agent loop shared by the workspace and note lenses.

One tool-calling loop, per-lens policy: an executor supplies the system
prompt, tools, conversation builder, live context, and every tool's
execution and event emission. The core owns the step accounting, token
streaming, retry guard, and tool-result feed-back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from app.config import settings
from app.services.llm import stream_llm_with_tools

logger = logging.getLogger(__name__)

MAX_PERSISTED_TOOL_ARGUMENT_CHARS = 4000

# Steps after the first of a turn receive a compact placeholder instead of the
# full workspace snapshot; the snapshot is only re-sent when a tool refreshed
# it. Dynamic observations are carried by the tool messages in history.
UNCHANGED_CONTEXT = (
    "## Workspace state: unchanged since the start of this turn. "
    "Tool results above show the latest observations."
)

# User-facing labels for internal tool names. Raw identifiers must never
# reach the UI ("Using run_r_cell"); each lens maps its tools here.
_FRIENDLY_TOOL_LABELS = {
    # Note lens
    "inspect_note": "Reviewing the notebook",
    "run_r_cell": "Running R cell",
    "add_note": "Adding a note",
    "promote_to_workspace": "Promoting to workspace",
    "inspect_data_files": "Checking data files",
    # Workspace lens
    "inspect_project": "Inspecting the project",
    "list_recipes": "Listing analysis recipes",
    "list_importable_datasets": "Finding example datasets",
    "list_files": "Listing workspace files",
    "search_workspace": "Searching the workspace",
    "search_bioc_books": "Searching Bioconductor books",
    "recall_memory": "Recalling project memory",
    "read_file": "Reading file",
    "read_results": "Reading results",
    "compare_results": "Comparing results",
    "inspect_failures": "Checking failed jobs",
    "validate_report": "Validating report",
    "run_r": "Running R inspection",
    "ask_user": "Asking you a question",
    "import_package_data": "Importing example dataset",
    "fetch_url": "Fetching file from URL",
    "plan_analysis": "Planning the analysis",
    "set_recipe_enabled": "Updating recipe settings",
    "update_recipe_parameters": "Updating recipe parameters",
    "set_analysis_variables": "Updating analysis variables",
    "run_recipe": "Running recipe",
    "run_analysis": "Running the analysis",
    "render_report": "Rendering the report",
    "repair_report": "Repairing the report",
    "rollback_analysis_configuration": "Rolling back configuration",
    "edit_project": "Editing project files",
    "queue_guidance": "Queuing guidance",
}


def friendly_tool_label(tool_name: str) -> str:
    """Human-readable status text for a tool call, never the raw identifier."""
    known = _FRIENDLY_TOOL_LABELS.get(tool_name)
    if known:
        return known
    # Fallback: humanize any unmapped snake_case name.
    words = str(tool_name).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Working"


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
    llm_provider_override: str | None = None
    llm_model_override: str | None = None
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

    def judge_intent(self, message: str) -> Any:
        """Semantic backstop for the fast path (async in implementations).

        Returns "conceptual", "needs_tools", or "needs_knowledge".
        """
        ...

    async def fast_path_events(self, message: str, *, intent: str = "conceptual") -> AsyncIterator[dict]:
        """Stream the direct answer events when use_fast_path returned True."""
        if False:
            yield {}
        ...

    def parallel_eligible(self, tool_name: str) -> bool:
        """Whether this read-only tool is safe to run concurrently.

        Tool calls in one LLM response that are all parallel-eligible run in
        worker threads via asyncio.gather. Keep this False for anything that
        touches the request-scoped database session or shared mutable state.
        """
        return False


async def run_agent_loop(
    executor: AgentExecutor,
    message: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict]:
    """Run one streaming agent turn with native LLM function calling.

    Text tokens stream directly to the client. Tool calls are executed
    and fed back to the model for the next iteration. Parallel-eligible
    read-only tool calls in the same step run concurrently; the full
    workspace snapshot is sent only on the first step (and after a tool
    refresh), with a compact placeholder on later steps.
    """
    started = time.monotonic()
    steps_run = 0
    tools_run = 0
    decision = "full"
    try:
        initial_events, handled = executor.initial_events(message)
        for event in initial_events:
            yield event
        if handled:
            decision = "handled"
            return

        if executor.use_fast_path(message):
            intent = "conceptual"
            judge = getattr(executor, "judge_intent", None)
            if settings.fast_path_judge_enabled and judge is not None:
                intent = await judge(message)
            if intent == "needs_tools":
                decision = "full_judged_tools"
            else:
                decision = f"fast_{intent}"
                from app.services.intent_fastpath import record_routing

                record_routing(
                    lens=type(executor).__name__,
                    message=message,
                    decision="fast",
                    reason="judge",
                    intent=intent,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                async for event in executor.fast_path_events(message, intent=intent):
                    yield event
                return

        messages = executor.build_messages(message)
        live_context = executor.build_live_context()
        collected_text = ""
        failed_tool_calls: dict[str, str] = {}
        context_refreshed = False

        for step in range(1, executor.max_steps + 1):
            steps_run = step
            if cancel_check and cancel_check():
                yield {"type": "cancelled", "message": executor.cancelled_message}
                return
            tool_calls_this_step: list[dict[str, Any]] = []
            step_text = ""
            current_live_context = (
                live_context if step == 1 or context_refreshed else UNCHANGED_CONTEXT
            )
            context_refreshed = False

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
                        live_context=current_live_context,
                        model_override=executor.llm_model_override,
                        provider_override=executor.llm_provider_override,
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

            # Execute each tool call; parallel-eligible reads run concurrently.
            for group in _partition_parallel_groups(tool_calls_this_step, executor):
                stop_events, outcomes = await _execute_tool_group(
                    executor,
                    group,
                    step=step,
                    step_text=step_text,
                    failed_tool_calls=failed_tool_calls,
                )
                if outcomes is None:
                    for event in stop_events:
                        yield event
                    return  # retry guard terminated the turn
                for tc, result in outcomes:
                    tools_run += 1
                    for event in result.events:
                        yield event
                    if result.end_turn:
                        if result.final_event is not None:
                            yield result.final_event
                        return

                    if result.observation.get("status") == "error" and result.record_failure:
                        failed_tool_calls[tool_signature(tc["name"], tc["arguments"])] = str(
                            result.observation.get("error") or "unknown error"
                        )[:2000]
                    status = "ok" if result.observation.get("status") != "error" else "error"
                    summary = result.summary or executor.summary_for(tc["name"], result.observation)
                    if result.emit_completed:
                        yield executor.tool_completed_event(
                            tc["name"],
                            tc["id"],
                            persistable_tool_arguments(tc["arguments"]),
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
                        context_refreshed = True

            # Continue to next step (model will see tool results)
            collected_text = ""  # Reset for next streaming round

        # Reached max steps
        for event in executor.max_steps_events():
            yield event
    finally:
        logger.info(
            "agent_loop lens=%s decision=%s steps=%d tools=%d duration_ms=%.0f msg=%r",
            type(executor).__name__,
            decision,
            steps_run,
            tools_run,
            (time.monotonic() - started) * 1000,
            (message or "")[:120],
        )


def _partition_parallel_groups(
    tool_calls: list[dict[str, Any]], executor: AgentExecutor
) -> list[list[dict[str, Any]]]:
    """Split a step's tool calls into runs of consecutive parallel-eligible
    calls (run as one concurrent batch) and lone sequential calls."""
    eligible = getattr(executor, "parallel_eligible", None) or (lambda name: False)
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for tc in tool_calls:
        if eligible(tc["name"]):
            current.append(tc)
        else:
            if current:
                groups.append(current)
                current = []
            groups.append([tc])
    if current:
        groups.append(current)
    return groups


def _execute_tool_in_thread(
    executor: AgentExecutor,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    step: int,
    tool_call_id: str,
    persisted_arguments: dict[str, Any],
    step_text: str,
) -> ToolCallResult:
    """Run one read-only tool call in a worker thread.

    Lens tool bodies are synchronous (file reads, registry lookups), so a
    fresh event loop in the worker thread is enough to produce the result.
    """
    return asyncio.run(
        executor.execute_tool(
            tool_name,
            arguments,
            step=step,
            tool_call_id=tool_call_id,
            persisted_arguments=persisted_arguments,
            step_text=step_text,
        )
    )


async def _execute_tool_group(
    executor: AgentExecutor,
    group: list[dict[str, Any]],
    *,
    step: int,
    step_text: str,
    failed_tool_calls: dict[str, str],
) -> tuple[list[dict], list[tuple[dict[str, Any], ToolCallResult]] | None]:
    """Execute one group of tool calls.

    Returns (stop_events, outcomes). When the retry guard trips, ``outcomes``
    is None and ``stop_events`` holds the events the caller must yield before
    ending the turn. Otherwise outcomes are (tool_call, result) pairs in input
    order; parallel-eligible groups run concurrently in worker threads.
    """
    prepared = []
    for tc in group:
        arguments = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
        signature = tool_signature(tc["name"], arguments)
        if executor.use_retry_guard and signature in failed_tool_calls:
            blocker = failed_tool_calls[signature]
            message = (
                f"I already tried {tc['name']} with those arguments and it failed, so I stopped retrying. "
                f"The exact blocker was: {blocker}"
            )
            return (
                [{"type": "token", "token": message}, executor.final_event(message)],
                None,
            )
        prepared.append((tc, arguments, persistable_tool_arguments(arguments)))

    if len(prepared) > 1:
        results = await asyncio.gather(*[
            asyncio.to_thread(
                _execute_tool_in_thread,
                executor,
                tc["name"],
                arguments,
                step=step,
                tool_call_id=tc["id"],
                persisted_arguments=persisted_arguments,
                step_text=step_text,
            )
            for tc, arguments, persisted_arguments in prepared
        ])
    else:
        tc, arguments, persisted_arguments = prepared[0]
        results = [await executor.execute_tool(
            tc["name"],
            arguments,
            step=step,
            tool_call_id=tc["id"],
            persisted_arguments=persisted_arguments,
            step_text=step_text,
        )]
    return [], [(tc, result) for (tc, _, _), result in zip(prepared, results)]
