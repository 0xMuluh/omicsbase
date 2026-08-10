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
from app.services.context_budget import bounded_json
from app.services.agent_failures import classify_tool_failure, is_retryable_failure
from app.services.llm import stream_llm_with_tools

logger = logging.getLogger(__name__)

MAX_PERSISTED_TOOL_ARGUMENT_CHARS = 4000


@dataclass
class TurnBudget:
    """Bounded per-turn resources shared by both agent lenses."""

    max_units: int
    max_tool_calls: int
    max_mutations: int
    units_used: int = 0
    tool_calls: int = 0
    mutation_count: int = 0
    llm_calls: int = 0
    generated_tokens: int = 0
    retrieved_chars: int = 0

    @classmethod
    def from_settings(cls) -> "TurnBudget":
        return cls(
            max_units=max(1, int(getattr(settings, "agent_max_budget_units", 12) or 12)),
            max_tool_calls=max(1, int(getattr(settings, "agent_max_tool_calls", 24) or 24)),
            max_mutations=max(1, int(getattr(settings, "agent_max_mutations", 4) or 4)),
        )

    def try_consume_tool(self, *, cost: int, mutating: bool) -> tuple[bool, str | None]:
        cost = max(1, int(cost))
        if self.tool_calls >= self.max_tool_calls:
            return False, f"the turn allows at most {self.max_tool_calls} tool calls"
        if mutating and self.mutation_count >= self.max_mutations:
            return False, f"the turn allows at most {self.max_mutations} mutations"
        if self.units_used + cost > self.max_units:
            return False, f"the turn has {self.max_units - self.units_used} budget units left, but this tool costs {cost}"
        self.tool_calls += 1
        self.units_used += cost
        if mutating:
            self.mutation_count += 1
        return True, None

    def record_llm_call(self) -> None:
        self.llm_calls += 1

    def record_generated(self, value: str) -> None:
        self.generated_tokens += max(0, (len(value or "") + 3) // 4)

    def record_retrieved(self, value: str) -> None:
        self.retrieved_chars += len(value or "")

    def snapshot(self) -> dict[str, int]:
        return {
            "units_used": self.units_used,
            "max_units": self.max_units,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "mutation_count": self.mutation_count,
            "max_mutations": self.max_mutations,
            "llm_calls": self.llm_calls,
            "generated_tokens": self.generated_tokens,
            "retrieved_chars": self.retrieved_chars,
        }

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


def _observation_failed(observation: dict[str, Any]) -> bool:
    if not isinstance(observation, dict):
        return False
    if str(observation.get("status") or "").lower() == "error":
        return True
    execution = observation.get("execution")
    if isinstance(execution, dict) and str(execution.get("status") or "").lower() in {"failed", "timed_out", "cancelled", "completed_with_errors"}:
        return True
    summary = observation.get("summary")
    return isinstance(summary, dict) and bool(summary.get("had_errors"))



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

    def deterministic_intent(self, message: str) -> str | None:
        """Return a safe route from request state, or ``None`` if ambiguous."""
        return None

    def tool_spec(self, tool_name: str) -> Any:
        """Return the lens-specific ToolSpec when the executor has one."""
        return None

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
            intent: str | None = None
            route_reason = "judge"
            deterministic = getattr(executor, "deterministic_intent", None)
            if deterministic is not None:
                intent = deterministic(message)
                if intent in {"conceptual", "needs_tools", "needs_knowledge"}:
                    route_reason = "request_state"
                    from app.services.intent_fastpath import record_routing

                    record_routing(
                        lens=type(executor).__name__,
                        message=message,
                        decision="deterministic",
                        reason="request_state",
                        intent=intent,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
                else:
                    intent = None

            if intent is None:
                judge = getattr(executor, "judge_intent", None)
                if settings.fast_path_judge_enabled and judge is not None:
                    intent = await judge(message)
                else:
                    intent = "needs_tools"
            if intent == "needs_tools":
                decision = f"full_{route_reason}_tools"
            else:
                decision = f"fast_{intent}"
                from app.services.intent_fastpath import record_routing

                record_routing(
                    lens=type(executor).__name__,
                    message=message,
                    decision="fast",
                    reason=route_reason,
                    intent=intent,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                async for event in executor.fast_path_events(message, intent=intent):
                    yield event
                return

        messages = executor.build_messages(message)
        live_context = executor.build_live_context()
        collected_text = ""
        failed_tool_calls: dict[str, dict[str, str]] = {}
        retry_attempted: set[str] = set()
        # Non-idempotent calls are guarded for this turn so reconnects cannot enqueue duplicates.
        seen_non_idempotent_calls: set[str] = set()
        context_refreshed = False
        turn_budget = TurnBudget.from_settings()

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
                turn_budget.record_llm_call()
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
                            turn_budget.record_generated(token)
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
                    retry_attempted=retry_attempted,
                    seen_non_idempotent_calls=seen_non_idempotent_calls,
                    budget=turn_budget,
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

                    observation = result.observation if isinstance(result.observation, dict) else {"status": "ok", "result": result.observation}
                    failed = _observation_failed(observation)
                    if failed:
                        failure_class = classify_tool_failure(observation)
                        observation.setdefault("failure_class", failure_class)
                        if result.record_failure:
                            failed_tool_calls[tool_signature(tc["name"], tc["arguments"])] = {
                                "message": str(observation.get("error") or observation.get("execution") or "unknown error")[:2000],
                                "class": failure_class,
                            }
                    observed_status = str(observation.get("status") or "")
                    status = "error" if failed else (observed_status if observed_status in {"duplicate", "unsupported", "budget_exhausted"} else "ok")
                    summary = result.summary or executor.summary_for(tc["name"], observation)
                    if result.emit_completed:
                        yield executor.tool_completed_event(
                            tc["name"],
                            tc["id"],
                            persistable_tool_arguments(tc["arguments"]),
                            status,
                            summary,
                            step,
                        )

                    # Feed a structurally bounded result back to the model.
                    tool_content = bounded_json(
                        observation,
                        executor.max_tool_chars,
                        priority_keys=("status", "summary", "error", "path", "revision", "sha256", "data", "truncated"),
                    )
                    turn_budget.record_retrieved(tool_content)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_content,
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
            "agent_loop lens=%s decision=%s steps=%d tools=%d budget=%s duration_ms=%.0f msg=%r",
            type(executor).__name__,
            decision,
            steps_run,
            tools_run,
            turn_budget.snapshot() if "turn_budget" in locals() else {},
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
    failed_tool_calls: dict[str, dict[str, str]],
    retry_attempted: set[str],
    seen_non_idempotent_calls: set[str],
    budget: TurnBudget,
) -> tuple[list[dict], list[tuple[dict[str, Any], ToolCallResult]] | None]:
    """Execute one group of tool calls.

    Returns (stop_events, outcomes). When the retry guard trips, ``outcomes``
    is None and ``stop_events`` holds the events the caller must yield before
    ending the turn. Otherwise outcomes are (tool_call, result) pairs in input
    order; parallel-eligible groups run concurrently in worker threads.
    """
    prepared = []
    blocked: dict[str, ToolCallResult] = {}
    for tc in group:
        arguments = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
        signature = tool_signature(tc["name"], arguments)
        failure = failed_tool_calls.get(signature)
        if executor.use_retry_guard and failure:
            failure_class = str(failure.get("class") or "unknown")
            if not is_retryable_failure(failure_class) or signature in retry_attempted:
                blocker = str(failure.get("message") or "unknown failure")
                message = (
                    f"I already tried {tc['name']} with those arguments and it failed, so I stopped retrying. "
                    f"Failure class: {failure_class}. The exact blocker was: {blocker}"
                )
                return (
                    [{"type": "token", "token": message}, executor.final_event(message)],
                    None,
                )
            retry_attempted.add(signature)
        idempotency = getattr(executor, "tool_idempotency", lambda name: "read_only")(tc["name"])
        if idempotency == "non_idempotent" and signature in seen_non_idempotent_calls:
            blocked[tc["id"]] = ToolCallResult(
                observation={
                    "status": "duplicate",
                    "error": (
                        f"Duplicate non-idempotent call suppressed: {tc['name']} "
                        "with the same arguments already ran in this turn."
                    ),
                },
                events=[{
                    "type": "tool_started",
                    "tool": tc["name"],
                    "reason": "Duplicate call suppressed; the original call is already in this turn.",
                    "step": step,
                }],
                summary="Duplicate call suppressed; the original call is already in this turn.",
                record_failure=False,
            )
            continue
        policy = getattr(executor, "tool_spec", lambda name: None)(tc["name"])
        cost = int(
            getattr(policy, "effective_budget", None)
            or getattr(policy, "budget", None)
            or 1
        )
        mutating = getattr(policy, "risk", None) in {"write", "execute"}
        allowed, budget_error = budget.try_consume_tool(cost=cost, mutating=mutating)
        if not allowed:
            message = f"Turn budget exhausted before {tc['name']}: {budget_error}."
            final_event = executor.final_event(message)
            if isinstance(final_event, dict):
                final_event["budget"] = budget.snapshot()
            blocked[tc["id"]] = ToolCallResult(
                observation={
                    "status": "budget_exhausted",
                    "error": message,
                    "budget": budget.snapshot(),
                },
                events=[{
                    "type": "tool_started",
                    "tool": tc["name"],
                    "reason": message,
                    "step": step,
                }],
                summary=message,
                end_turn=True,
                final_event=final_event,
                record_failure=False,
            )
            continue
        if idempotency == "non_idempotent":
            seen_non_idempotent_calls.add(signature)
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
    elif prepared:
        tc, arguments, persisted_arguments = prepared[0]
        results = [await executor.execute_tool(
            tc["name"],
            arguments,
            step=step,
            tool_call_id=tc["id"],
            persisted_arguments=persisted_arguments,
            step_text=step_text,
        )]
    else:
        results = []
    by_id = dict(blocked)
    by_id.update({tc["id"]: result for (tc, _, _), result in zip(prepared, results)})
    return [], [(tc, by_id[tc["id"]]) for tc in group]
