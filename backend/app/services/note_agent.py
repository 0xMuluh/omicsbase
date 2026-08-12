"""Autonomous agent loop for the linear Chat/Notes notebook."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from sqlalchemy import func

from typing import Any, AsyncIterator, Callable

from app.config import settings
from app.models.notes import NoteCell, NoteCellRevision, NoteThread
from app.services.agent_core import ToolCallResult, friendly_tool_label, persistable_tool_arguments, run_agent_loop
from app.services.tool_specs import NOTE_TOOL_SPECS, TOOL_REGISTRY
from app.services.context_budget import bounded_json
from app.services.agent_runtime import normalise_cell_type

logger = logging.getLogger(__name__)

MAX_NOTE_TOOL_CHARS = 12_000
MAX_NOTE_HISTORY = 12
MAX_NOTE_HISTORY_CHARS = 24_000
# Absolute safety ceiling for one note turn; the configured budget defaults
# to this and can be lowered via NOTE_AGENT_MAX_STEPS.
MAX_NOTE_STEPS = 24

NOTE_AGENT_SYSTEM_PROMPT = """You are the OmicsBase autonomous agent for a linear scientific Chat/Notes notebook.

This thread is an exploratory notebook, not a Quarto Workspace or published report. Greetings and one-word exchanges are answered naturally and concisely, without added structure or formatting. Work only from the supplied notebook context and the explicitly permitted notebook tools. Do not use arbitrary external sources, install packages, fetch datasets, render reports, or edit the Workspace unless the user explicitly asks to promote a tested step.

Treat this NoteThread as a conversational executable scientific notebook,
not a scripted notebook generator. For each request, decide whether it needs
a direct answer, inspection of existing notebook or data state, computation,
durable markdown narration, scientific reference lookup, or a combination.
Do not impose a fixed `add_note` -> `run_r_cell` sequence.

Answer directly when computation is unnecessary. Do not create notebook cells
for purely conversational or conceptual scientific questions.

Use `run_r_cell` only when the user asks to calculate, demonstrate, transform,
plot, test, fit, compare, or otherwise produce a result that requires
execution, or when a claim materially requires computation. Use `add_note`
only when durable narration preserves an important choice, assumption,
interpretation, methodology, or explicitly requested documentation. Use both
when a computed result should be preserved with explanatory context. Never
add a note merely because an analysis step exists.

When computation is needed, keep each cell focused on one coherent operation.
Exceed that boundary only when the operations are tightly related or when a
small correction is needed after an execution error.

Notebook cells share one persistent R workspace. Variables created earlier remain available later, and previously attached packages remain attached. Reuse existing objects and do not reload large datasets unnecessarily. Load only packages or data that are missing.

Keep computational cells focused on the requested operation. Do not combine
unrelated transformations, analyses, tests, or plots in one cell, but combine
tightly coupled expressions when that makes the result clearer and safer.
Reuse intermediate objects instead of recomputing them. Do not overwrite
existing workspace objects unless the overwrite is intentional and explained.

Use markdown notes for narration. Do not use `cat()`, `message()`, or `print()` for prose commentary. Return substantive result objects as the final expression of the cell, and use explicit printing only when required to display the actual result.

Inspect before acting when the request depends on existing state. Use `inspect_note` to review prior cells, executions, artifacts, and workspace objects; use `inspect_data_files` before reading an attached file whose path or schema is not already established. Prefer an existing execution result or artifact over rerunning the computation. Prefer the narrowest reliable inspection tool before ad-hoc R inspection. Do not infer object content from its name alone; inspect structure, dimensions, names, classes, and metadata when needed. Ordinary NoteThread interaction must not depend on UI selection state.

When the user requests a data-specific analysis and relevant data are available, demonstrate it with real execution:

* If a relevant object already exists in `workspace_objects`, use it directly.
* If the user refers to an attached file and the data are not already loaded with clear provenance, call `inspect_data_files` first.
* Read the file using the returned `r_path`.
* Never invent filenames, columns, observations, sample groups, p-values, or results.

Execution is asynchronous when run_r_cell returns a queued or running execution. A queued or running cell is not a successful result: wait for the execution, resume the same logical request, inspect the actual result, and only then interpret it. Never claim a computed value, successful plot, or confirmed analysis without a completed successful execution.

If a cell fails, inspect the actual failure, diagnose it, and make the smallest justified correction before rerunning. Do not repeatedly retry environment or dependency failures; explain the blocker and identify the missing requirement. For purely conceptual questions that do not require calculation, provide a markdown explanation without running R. A small computation is appropriate only when it materially improves the explanation or the user asks for a demonstration.

For methodological questions involving omics or Bioconductor workflows, use search_bioc_books when references would improve the answer or computation. This includes questions about methodology, assumptions, interpretation, accepted practice, and recommendations. References guide the answer or computation; they do not automatically trigger computation. A returned source containing R code is not evidence that the user requested execution. When explaining a concept or method, prefer a relevant worked example from the books, adapt it rather than inventing one, and run it only when the request calls for execution or the small computation materially improves the explanation. Treat excerpts as methodological guidance, not evidence about the user data. Preserve and cite source metadata.

When the user asks to demonstrate, show, or work through a method or example, use search_bioc_books when scientific grounding would improve the demonstration, cite any returned sources, and then perform the requested work. When the demonstration requires computation, use a small seeded run_r_cell example unless the user data or workspace must be inspected.

Use `list_skills` and `load_skill` only when a specialized skill pack is needed for the requested workflow or report, and load only relevant references. Do not load skills for ordinary conceptual answers; loaded skill text is guidance, not evidence about the notebook data.

For stochastic procedures, set and display a reproducible seed. Avoid unexplained changes to the working directory, global options, contrasts, or other session-wide state. Preserve alignment between assays, samples, features, and metadata.

Distinguish clearly between facts supplied by the user or data, values computed by OmicsBase, inferences, suggestions, and unknowns requiring clarification. Never silently resolve consequential study-design ambiguity; surface it and ask a focused question when it could change the analysis or interpretation.

Do not edit the Workspace. When the user explicitly asks to move a tested notebook step into the report, use `promote_to_workspace` to copy the validated cell into the project code directory. Promotion defaults to creating a new file; updating an existing file requires an explicit base_sha256 and an append or replace strategy.

The final chat response should not repeat or enumerate notebook cells. In normal cases, use 1–3 sentences summarizing the substantive result or stating that results appear inline. If execution is blocked, briefly state the blocking issue and the exact missing input or dependency.

Return natural language unless a tool is needed. Tool arguments must be valid JSON."""


NOTE_TOOLS = [spec.as_openai() for spec in NOTE_TOOL_SPECS]

def _latest_revision(cell: Any) -> Any | None:
    revisions = list(getattr(cell, "revisions", []) or [])
    return revisions[-1] if revisions else None


def conversation_from_cells(cells: list[Any]) -> list[dict[str, str]]:
    """Turn durable notebook cells into a compact, replayable conversation."""
    messages: list[dict[str, str]] = []
    for cell in sorted(cells, key=lambda item: (int(item.position or 0), item.created_at)):
        revision = _latest_revision(cell)
        if revision is None or not str(revision.content or "").strip():
            continue
        cell_type = str(revision.cell_type or "markdown")
        content = str(revision.content)
        if cell_type == "code":
            language = str(revision.language or "r")
            content = f"Generated {language} cell:\n```{language}\n{content}\n```"
        elif cell_type == "provenance":
            content = f"Provenance note:\n{content}"
        role = "user" if cell_type == "agent" else "assistant"
        messages.append({"role": role, "content": content[:MAX_NOTE_TOOL_CHARS]})
    selected = messages[-MAX_NOTE_HISTORY:]
    compacted: list[dict[str, str]] = []
    remaining = MAX_NOTE_HISTORY_CHARS
    for message in reversed(selected):
        content = str(message.get("content") or "")
        if remaining <= 0:
            break
        if len(content) > remaining:
            if remaining < 160:
                break
            content = content[: max(1, remaining - 36)] + "\n…[older notebook history omitted]"
        compacted.append({"role": str(message.get("role") or "assistant"), "content": content})
        remaining -= len(content)
    return list(reversed(compacted))


def _live_context(context: dict[str, Any]) -> str:
    # Cell contents are already carried as replayable messages. Keep the live
    # snapshot cheap; inspect_note explicitly refreshes the durable cell view.
    snapshot = dict(context or {})
    snapshot.pop("cells", None)
    return (
        "## Current linear notebook context\n```json\n"
        + bounded_json(snapshot, MAX_NOTE_TOOL_CHARS, priority_keys=("thread", "workspace_objects", "data_files", "workspace"))
        + "\n```"
    )


def _tool_summary(name: str, observation: dict[str, Any]) -> str:
    if observation.get("status") == "error":
        error = observation.get("error", "unknown error")
        return f"{name} failed: {error}"
    if name == "inspect_note":
        return "Notebook context inspected"
    if name == "run_r_cell":
        execution = observation.get("execution") or {}
        execution_status = execution.get("status", "queued")
        preview = str(((execution.get("result_metadata") or {}).get("stdout_preview") or ""))[:160]
        if execution_status in {"completed", "completed_with_errors"}:
            summary = "R cell completed" if execution_status == "completed" else "R cell completed with errors"
            if preview:
                summary += ": " + preview
            return summary
        if execution_status in {"failed", "timed_out", "cancelled"}:
            error = str(execution.get("error") or "")[:160] or execution_status
            return f"R cell {execution_status}: {error}"
        return f"R cell {execution_status}"
    if name == "add_note":
        return "Markdown note appended to the notebook"
    if name == "promote_to_workspace":
        return f"Promoted to {observation.get('path', 'the workspace')}"
    if name == "inspect_data_files":
        files = observation.get("files") or []
        if observation.get("status") == "error":
            return "Data file inspection failed"
        if not files:
            return "No data files attached to this notebook"
        names = ", ".join(str(item.get("name")) for item in files[:5])
        return f"Inspected {len(files)} data file(s): {names}"
    return f"{name} completed"


def _fallback_message(error: Exception | None = None) -> str:
    if error is not None:
        logger.exception("NoteThread agent failed: %s", error)
    return (
        "I preserved your question in this notebook, but I could not complete the agent turn. "
        "No unrequested computation was run. You can retry or continue from the saved cells."
    )


ActionHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def append_note_cell(
    db: Any,
    thread: NoteThread,
    *,
    cell_type: str,
    content: str,
    language: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> NoteCell:
    """Append one durable cell and revision while preserving notebook order."""
    if thread.status != "active":
        raise ValueError("Cannot add cells to an archived NoteThread")
    value = str(content or "")
    if not value.strip():
        raise ValueError("Note cell content cannot be empty")

    db.query(NoteThread.id).filter(NoteThread.id == str(thread.id)).with_for_update().scalar()
    current_max = (
        db.query(func.max(NoteCell.position))
        .filter(NoteCell.thread_id == str(thread.id))
        .scalar()
    )
    cell = NoteCell(
        thread_id=str(thread.id),
        position=(current_max if current_max is not None else -1) + 1,
        status="active",
    )
    cell.revisions.append(
        NoteCellRevision(
            revision=1,
            cell_type=normalise_cell_type(cell_type),
            language=language,
            content=value,
            revision_metadata=metadata,
            created_by=created_by,
        )
    )
    thread.updated_at = datetime.now(timezone.utc)
    db.add(cell)
    db.commit()
    db.refresh(cell)
    return cell


async def stream_note_agent(
    *,
    message: str,
    cells: list[Any],
    context: dict[str, Any],
    action_handler: ActionHandler | None = None,
    knowledge_search_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    max_steps: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one autonomous NoteThread turn using only notebook-safe tools."""
    executor = NoteAgentExecutor(
        message=message,
        cells=cells,
        context=context,
        action_handler=action_handler,
        knowledge_search_handler=knowledge_search_handler,
        max_steps=max_steps,
    )
    async for event in run_agent_loop(executor, message, cancel_check=cancel_check):
        yield event


class NoteAgentExecutor:
    """NoteThread lens: four notebook-safe tools on the shared agent core."""

    def __init__(
        self,
        *,
        message: str,
        cells: list[Any],
        context: dict[str, Any],
        action_handler: ActionHandler | None = None,
        knowledge_search_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ):
        self.cells = cells
        self.context = context
        self.action_handler = action_handler
        self.knowledge_search_handler = knowledge_search_handler
        self.max_steps = max(2, min(int(max_steps or getattr(settings, "note_agent_max_steps", MAX_NOTE_STEPS) or MAX_NOTE_STEPS), MAX_NOTE_STEPS))
        self.max_tokens = int(getattr(settings, "agent_max_output_tokens", 16000) or 16000)
        self.max_tool_chars = MAX_NOTE_TOOL_CHARS
        self.system_prompt = NOTE_AGENT_SYSTEM_PROMPT
        self.tools = NOTE_TOOLS
        self.use_retry_guard = True
        self.cancelled_message = "This NoteThread run was cancelled."
        self.default_final_message = "I could not produce a grounded notebook response."
        from app.services.llm import resolve_target

        self.llm_provider_override, self.llm_model_override = resolve_target("agent")

    def initial_events(self, message: str) -> tuple[list[dict], bool]:
        current_message = (message or "").strip()
        if not current_message:
            return [{"type": "final", "message": "Tell me what you want to investigate in this notebook."}], True
        return [{"type": "status", "status": "thinking", "message": "Thinking about the notebook question"}], False

    def build_messages(self, message: str) -> list[dict]:
        messages: list[dict[str, Any]] = conversation_from_cells(self.cells)
        messages.append({"role": "user", "content": message})
        return messages

    def build_live_context(self) -> str:
        return _live_context(self.context)

    def fallback_events(self, exc: Exception) -> list[dict]:
        fallback = _fallback_message(exc)
        return [
            {"type": "error", "message": fallback},
            {"type": "final", "message": fallback},
        ]

    def final_event(self, message: str) -> dict:
        return {"type": "final", "message": message}

    def max_steps_events(self) -> list[dict]:
        return [{
            "type": "final",
            "message": (
                "I inspected the notebook but reached the safe action limit for this turn. "
                "The persisted cells and any queued execution remain available above."
            ),
        }]

    def tool_completed_event(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        status: str,
        summary: str,
        step: int,
    ) -> dict:
        return {
            "type": "tool_completed",
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "status": status,
            "summary": summary,
            "step": step,
        }

    def summary_for(self, tool_name: str, observation: dict) -> str:
        return _tool_summary(tool_name, observation)

    def tool_spec(self, tool_name: str):
        return TOOL_REGISTRY.get(tool_name, lens="note")

    def parallel_eligible(self, tool_name: str) -> bool:
        spec = self.tool_spec(tool_name)
        return bool(spec and spec.parallel)

    def use_fast_path(self, message: str) -> bool:
        from app.services.intent_fastpath import is_simple_question
        return is_simple_question(message)

    def deterministic_intent(self, message: str) -> str | None:
        from app.services.intent_fastpath import deterministic_intent

        context = self.context if isinstance(self.context, dict) else {}
        return deterministic_intent(
            message,
            lens="note",
            notebook_state=bool(
                self.cells
                or context.get("cells")
                or context.get("workspace_objects")
                or context.get("data_files")
            ),
            pending_question=bool(context.get("pending_question")),
        )

    async def judge_intent(self, message: str) -> str:
        from app.services.intent_fastpath import classify_intent

        return await classify_intent(message)

    async def fast_path_events(self, message: str, *, intent: str = "conceptual") -> AsyncIterator[dict]:
        from app.services.intent_fastpath import stream_simple_answer
        knowledge_context = (
            self._knowledge_seed(message)
            if intent == "needs_knowledge"
            else None
        )
        async for event in stream_simple_answer(message, knowledge_context=knowledge_context):
            yield event

    def _knowledge_seed(self, message: str) -> str | None:
        """Ground knowledge-seeking fast-path answers with book excerpts."""
        if self.knowledge_search_handler is None:
            return None
        try:
            observation = self.knowledge_search_handler({"query": message, "limit": 5, "channel": "stable"})
            from app.services.intent_fastpath import format_knowledge_seed

            return format_knowledge_seed((observation or {}).get("matches") or [])
        except Exception as exc:
            logger.warning("Fast-path knowledge seeding failed: %s", exc)
            return None

    async def legacy_llm_step(self, messages: list[dict], *, step: int) -> None:
        return None

    def tool_idempotency(self, tool_name: str) -> str:
        spec = TOOL_REGISTRY.get(tool_name, lens="note")
        return spec.idempotency if spec is not None else "read_only"

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
        events: list[dict[str, Any]] = [{
            "type": "tool_started",
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "step": step,
            "message": friendly_tool_label(tool_name),
        }]
        if tool_name == "list_skills":
            from app.services.skills import list_skills

            observation = list_skills(arguments)
        elif tool_name == "load_skill":
            from app.services.skills import load_skill

            observation = load_skill(
                str(arguments.get("skill") or ""),
                arguments.get("references"),
                arguments.get("max_chars", 12_000),
            )
        elif tool_name not in {"inspect_note", "search_bioc_books", "run_r_cell", "add_note", "promote_to_workspace", "inspect_data_files"}:
            observation = {"status": "error", "error": f"Unknown NoteThread tool: {tool_name}"}
        elif self.action_handler is None:
            observation = {"status": "error", "error": "NoteThread tools are unavailable"}
        else:
            try:
                observation = await self.action_handler(tool_name, arguments)
                if not isinstance(observation, dict):
                    observation = {"status": "ok", "result": observation}
            except Exception as exc:
                logger.exception("NoteThread tool %s failed: %s", tool_name, exc)
                observation = {"status": "error", "error": str(exc)[:4000]}

        if observation.get("cell"):
            events.append({
                "type": "note_cell",
                "cell": observation["cell"],
                "turn_id": observation.get("turn_id"),
            })
        wait_for = None
        final_event = None
        execution = observation.get("execution")
        if execution:
            events.append({
                "type": "execution_queued",
                "execution": execution,
                "cell": observation.get("cell"),
                "turn_id": observation.get("turn_id"),
                "tool_arguments": persistable_tool_arguments(arguments),
            })
            execution_status = str(execution.get("status") or "").lower()
            if execution_status in {"queued", "running", "cancel_requested"}:
                wait_for = {"kind": "execution", "id": str(execution.get("id") or "")}
                final_event = {
                    "type": "final",
                    "message": "The R cell is still running. I will continue this request when its result is available.",
                    "continuation_pending": True,
                }
        return ToolCallResult(
            observation=observation,
            events=events,
            wait_for=wait_for,
            final_event=final_event,
        )



__all__ = ["NOTE_AGENT_SYSTEM_PROMPT", "NOTE_TOOLS", "append_note_cell", "conversation_from_cells", "stream_note_agent"]
