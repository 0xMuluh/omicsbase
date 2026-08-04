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
from app.services.agent_core import ToolCallResult, run_agent_loop
from app.services.agent_runtime import normalise_cell_type

logger = logging.getLogger(__name__)

MAX_NOTE_TOOL_CHARS = 12_000
MAX_NOTE_HISTORY = 12
MAX_NOTE_STEPS = 8

NOTE_AGENT_SYSTEM_PROMPT = """You are OmicsBase autonomous agent for a linear scientific Chat/Notes notebook.

This thread is an exploratory notebook, not a Quarto Workspace or published report. Work only from the notebook context supplied to you.

Build the notebook like a literate analysis, not a code dump:
- Structure the turn as alternating steps: use add_note to insert a short markdown explanation BEFORE each code step, then run_r_cell for that step. The final answer only summarises the results; the notebook itself carries the explanation.
- Split computation into logical steps (e.g. load libraries, load data, prepare, analyse) as separate run_r_cell calls, normally 2-4 code cells per turn. Cells of this notebook share one persistent R workspace, exactly like Jupyter/Colab: variables defined in an earlier cell are visible in later cells, and attached libraries are re-attached automatically. Load data in one cell and reference it in the next; there is no need to repeat setup.
- Keep every code cell small and single-purpose. Never merge several analyses, several plots, or unrelated steps into one cell: one cell = one library load / one data-prep step / one plot / one statistical test. If a cell would do two distinct things, split it. Reuse objects from earlier cells instead of re-extracting or recomputing them.
- Keep each note concise (2-4 sentences). Never narrate with cat() or print() inside code cells; markdown notes are the narration. Only print/cat values that are the actual computation result the user should see.
- Never restate or re-list the cells you queued in the final message — the user can see every cell in the notebook. Keep the final answer to 1-3 sentences: a brief note that the cells above are running and their results will appear inline, or (when no computation was needed) the direct answer itself.
- Demonstrate with real runs when data is available: if the notebook already contains data (an object loaded by an earlier cell, or an uploaded file), do not just present code — call run_r_cell on the smallest real example using that data and show the actual output. The execution result appears inline and proves the code works. If the notebook has no data and the question is conceptual, a plain markdown explanation is enough and code cells are optional.
- The run_r_cell result is returned to you with its real output or error. Check it: if the output is wrong or the cell failed, diagnose the error from the output and run a corrected cell (stay within the step budget). If the failure is environmental (missing data or a missing package), explain that instead of looping.
- Reuse the shared workspace: the notebook context lists workspace_objects — variables already defined by earlier cells. Use them directly and never reload a large dataset that an earlier cell already loaded (that wastes memory). Only load what is missing.
- Inspect the notebook before relying on earlier results.
- If a question genuinely requires a calculation, call run_r_cell with the smallest reproducible R cell.
- For methodological questions or reusable analysis code, call search_bioc_books first when the catalog has relevant material.
- Treat book excerpts as methodological guidance, not evidence about the user's data; preserve and cite the returned source metadata.
- Never invent files, columns, observations, p-values, or results. If the notebook lacks the required data, say what is missing.
- Do not install packages, fetch data, or render a report from this thread. Do not edit the Workspace either — except that when the user explicitly asks to move a tested step into the report, promote_to_workspace may copy a cell into the project's code directory.

Return natural language unless a tool is needed. Tool arguments must be valid JSON."""


def _tool_def(name: str, description: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        },
    }


NOTE_TOOLS = [
    _tool_def(
        "inspect_note",
        "Inspect the current linear notebook, including prior cells and completed execution previews.",
    ),
    _tool_def(
        "search_bioc_books",
        "Search the pinned QMD-derived Bioconductor books for relevant explanations, assumptions, and reusable R examples. Prefer the stable channel unless the user explicitly asks about development material.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The scientific or coding question to search for."},
                "channel": {"type": "string", "enum": ["stable", "preview"], "default": "stable"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                "book": {"type": "string", "description": "Optional curated book slug."}
            },
            "required": ["query"]
        },
    ),
    _tool_def(
        "run_r_cell",
        "Persist and queue a minimal R computation when the user question requires it. Do not use for explanations that need no calculation.",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The complete R cell to persist and execute."},
                "purpose": {"type": "string", "description": "What scientific question this cell checks."},
                "parameters": {"type": "object", "description": "Explicit parameters used by the cell."},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
            },
            "required": ["code"],
        },
    ),
    _tool_def(
        "add_note",
        "Insert a concise markdown explanation into the notebook at this point, between code steps. Use this for all narration; never narrate with cat() inside code cells.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The markdown text for the note (2-4 sentences)."},
            },
            "required": ["text"],
        },
    ),
    _tool_def(
        "promote_to_workspace",
        "Copy a tested R cell into the project's code directory (e.g. data_processing.R) so the next report build adopts it. Only usable in a notebook attached to a project, and only when the user asks to move the work into the report. The path is relative to the project code directory.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file name under code/, e.g. data_processing.R"},
                "content": {"type": "string", "description": "The full R content to write"},
                "purpose": {"type": "string", "description": "What this promoted step does in the pipeline"},
            },
            "required": ["path", "content"],
        },
    ),
]


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
    return messages[-MAX_NOTE_HISTORY:]


def _live_context(context: dict[str, Any]) -> str:
    return (
        "## Current linear notebook context\n```json\n"
        + json.dumps(context, indent=1, default=str)[:MAX_NOTE_TOOL_CHARS]
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
    max_steps: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one autonomous NoteThread turn using only notebook-safe tools."""
    executor = NoteAgentExecutor(
        message=message,
        cells=cells,
        context=context,
        action_handler=action_handler,
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
        max_steps: int | None = None,
    ):
        self.cells = cells
        self.context = context
        self.action_handler = action_handler
        self.max_steps = max(2, min(int(max_steps or getattr(settings, "agent_max_steps", 6) or 6), MAX_NOTE_STEPS))
        self.max_tokens = 3000
        self.max_tool_chars = MAX_NOTE_TOOL_CHARS
        self.system_prompt = NOTE_AGENT_SYSTEM_PROMPT
        self.tools = NOTE_TOOLS
        self.use_retry_guard = False
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

    def use_fast_path(self, message: str) -> bool:
        from app.services.intent_fastpath import is_simple_question
        return is_simple_question(message)

    async def fast_path_events(self, message: str) -> AsyncIterator[dict]:
        from app.services.intent_fastpath import stream_simple_answer
        async for event in stream_simple_answer(message):
            yield event

    async def legacy_llm_step(self, messages: list[dict], *, step: int) -> None:
        return None

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
            "message": f"Using {tool_name}",
        }]
        if tool_name not in {"inspect_note", "search_bioc_books", "run_r_cell", "add_note", "promote_to_workspace"}:
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
        if observation.get("execution"):
            events.append({
                "type": "execution_queued",
                "execution": observation["execution"],
                "cell": observation.get("cell"),
                "turn_id": observation.get("turn_id"),
            })
        return ToolCallResult(observation=observation, events=events)



__all__ = ["NOTE_AGENT_SYSTEM_PROMPT", "NOTE_TOOLS", "append_note_cell", "conversation_from_cells", "stream_note_agent"]
