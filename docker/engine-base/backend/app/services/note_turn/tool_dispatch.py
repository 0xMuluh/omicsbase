"""Tool dispatch for one durable NoteThread turn."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import BackgroundTasks

from app.services.note_payloads import cell_payload as _cell_payload, execution_payload as _execution_payload
from app.services.note_store import get_tenant_thread as _get_note_thread_for_tenant
from app.services.note_turn_context import build_note_agent_context as _note_agent_context
from app.services.note_promotion import promote_cell_to_workspace as _promote_cell_to_workspace
from app.config import settings
from app.models.project import Project
from app.schemas.schemas import NoteCellExecutionCreate, NoteThreadTurnRequest
from app.services.note_agent import append_note_cell


class NoteTurnToolDispatcher:
    """Own notebook tool policy and state for one turn.

    The dispatcher deliberately owns generated-cell counters and knowledge
    citations. The event-stream layer reads those values for durable telemetry
    and completion metadata, but does not implement tool policy itself.
    """

    def __init__(
        self,
        *,
        db,
        thread_id: str,
        tenant_id: str,
        turn_id: str,
        message: str,
        data: NoteThreadTurnRequest,
        background_tasks: BackgroundTasks,
        execution_observation: Callable[[dict, dict, int], Awaitable[dict]],
    ) -> None:
        self.db = db
        self.thread_id = thread_id
        self.tenant_id = tenant_id
        self.turn_id = turn_id
        self.message = message
        self.data = data
        self.background_tasks = background_tasks
        self.execution_observation = execution_observation
        self.generated_code_cells = 0
        self.generated_note_cells = 0
        self.max_generated_code_cells = max(
            1,
            int(getattr(settings, "note_agent_max_generated_code_cells", 8) or 8),
        )
        self.knowledge_sources: list[str] = []

    def _record_citations(self, result: dict) -> None:
        for match in result.get("matches") or []:
            citation = str(match.get("citation") or "").strip()
            if citation and citation not in self.knowledge_sources:
                self.knowledge_sources.append(citation)

    def knowledge_search_handler(self, arguments: dict) -> dict:
        from app.services.bioc_knowledge import search_bioc_knowledge

        query = str(arguments.get("query") or self.message).strip()
        channel = str(arguments.get("channel") or "stable").strip().lower()
        try:
            limit = int(arguments.get("limit") or 3)
        except (TypeError, ValueError):
            limit = 3
        result = search_bioc_knowledge(
            self.db,
            query,
            channel=channel,
            limit=max(1, min(limit, 8)),
            source_slug=str(arguments.get("book") or "").strip() or None,
        )
        self._record_citations(result)
        return result

    async def action_handler(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        fresh_thread = _get_note_thread_for_tenant(self.db, self.thread_id, self.tenant_id)
        if tool_name == "inspect_note":
            return {
                "status": "ok",
                "context": _note_agent_context(self.db, fresh_thread),
                "turn_id": self.turn_id,
            }
        if tool_name == "inspect_data_files":
            from app.services.note_data import list_thread_data_files

            project = None
            if fresh_thread.project_id:
                project = self.db.query(Project).filter(Project.id == fresh_thread.project_id).first()
            return {
                "status": "ok",
                "files": list_thread_data_files(fresh_thread, project=project),
                "turn_id": self.turn_id,
            }
        if tool_name == "promote_to_workspace":
            return _promote_cell_to_workspace(
                self.db,
                fresh_thread,
                arguments,
                turn_id=self.turn_id,
            )
        if tool_name == "search_bioc_books":
            from app.services.bioc_knowledge import search_bioc_knowledge

            query = str(arguments.get("query") or self.message).strip()
            channel = str(arguments.get("channel") or "stable").strip().lower()
            try:
                limit = int(arguments.get("limit") or 5)
            except (TypeError, ValueError):
                limit = 5
            result = search_bioc_knowledge(
                self.db,
                query,
                channel=channel,
                limit=max(1, min(limit, 8)),
                source_slug=str(arguments.get("book") or "").strip() or None,
            )
            self._record_citations(result)
            return {**result, "turn_id": self.turn_id}
        if tool_name == "add_note":
            text = str(arguments.get("text") or "").strip()
            if not text:
                return {"status": "error", "error": "The note text was empty", "turn_id": self.turn_id}
            if len(text) > 8_000:
                return {"status": "error", "error": "The note exceeds the 8000 character limit", "turn_id": self.turn_id}
            if self.generated_note_cells >= 6:
                return {"status": "error", "error": "This turn reached the generated note limit", "turn_id": self.turn_id}
            self.generated_note_cells += 1
            note_cell = append_note_cell(
                self.db,
                fresh_thread,
                cell_type="markdown",
                content=text,
                metadata={
                    "turn_id": self.turn_id,
                    "role": "assistant",
                    "generated_by": "note_agent",
                    "knowledge_sources": self.knowledge_sources[-12:],
                },
                created_by="agent",
            )
            return {"status": "ok", "cell": _cell_payload(note_cell), "turn_id": self.turn_id}
        if tool_name != "run_r_cell":
            return {"status": "error", "error": "Unsupported NoteThread action", "turn_id": self.turn_id}

        code = str(arguments.get("code") or "").strip()
        if not code:
            return {"status": "error", "error": "The R cell was empty", "turn_id": self.turn_id}
        if len(code) > 2_000_000:
            return {"status": "error", "error": "The R cell exceeds the 2 MB limit", "turn_id": self.turn_id}
        if self.generated_code_cells >= self.max_generated_code_cells:
            return {"status": "error", "error": "This turn reached the generated R-cell limit", "turn_id": self.turn_id}
        parameters = arguments.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        timeout_seconds = arguments.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = int(settings.note_execution_default_timeout_seconds)
        else:
            try:
                timeout_seconds = int(timeout_seconds)
            except (TypeError, ValueError):
                return {"status": "error", "error": "timeout_seconds must be an integer", "turn_id": self.turn_id}
        purpose = str(arguments.get("purpose") or "Notebook computation")[:1000]

        # A resumed/replayed turn must not append the same generated cell and
        # execute it again. The AgentRun id is the durable turn identity; the
        # immutable revision content is the call identity.
        for existing_cell in fresh_thread.cells:
            for existing_revision in existing_cell.revisions:
                metadata = existing_revision.revision_metadata or {}
                if (
                    str(metadata.get("turn_id") or "") == self.turn_id
                    and existing_revision.cell_type == "code"
                    and str(existing_revision.content or "").strip() == code
                ):
                    prior_executions = list(existing_revision.executions or [])
                    if prior_executions:
                        prior_execution = max(prior_executions, key=lambda item: item.created_at)
                        replay = await self.execution_observation(
                            _execution_payload(prior_execution),
                            _cell_payload(existing_cell),
                            timeout_seconds,
                        )
                        replay["duplicate_call"] = True
                        return replay

        self.generated_code_cells += 1
        cell = append_note_cell(
            self.db,
            fresh_thread,
            cell_type="code",
            language="r",
            content=code,
            metadata={
                "turn_id": self.turn_id,
                "role": "assistant",
                "generated_by": "note_agent",
                "purpose": purpose,
                "knowledge_sources": self.knowledge_sources[-12:],
            },
            created_by="agent",
        )
        cell_payload = _cell_payload(cell)
        if not self.data.auto_execute:
            return {"status": "ok", "cell": cell_payload, "turn_id": self.turn_id}

        from app.api.projects_note_executions import execute_note_cell, execute_standalone_note_cell

        execution_request = NoteCellExecutionCreate(
            revision=1,
            parameters=parameters,
            timeout_seconds=timeout_seconds,
            cache_policy="off",
            idempotency_key=(
                "agent:"
                + self.turn_id
                + ":"
                + hashlib.sha256(
                    (code + json.dumps(parameters, sort_keys=True, default=str)).encode("utf-8")
                ).hexdigest()[:48]
            ),
        )
        if fresh_thread.project_id:
            execution_payload = execute_note_cell(
                str(fresh_thread.project_id),
                self.thread_id,
                str(cell.id),
                execution_request,
                self.background_tasks,
                self.db,
                self.tenant_id,
            )
        else:
            execution_payload = execute_standalone_note_cell(
                self.thread_id,
                str(cell.id),
                execution_request,
                self.background_tasks,
                self.db,
                self.tenant_id,
            )
        return await self.execution_observation(execution_payload, cell_payload, timeout_seconds)
