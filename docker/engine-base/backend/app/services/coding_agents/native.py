"""Direct Native Coding Agent Backend for OmicsBase.

Implements the CodingAgentBackend protocol using direct LLM streaming with
deterministic in-process tools (view_file, replace_file_content, write_to_file,
list_dir, grep_search, run_command). 

Eliminates secondary LLM indirection (OpenCode/Codex) and provides fast,
deterministic 2-layer agent execution modeled directly on Antigravity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from app.config import settings
from app.services.coding_agents.contract import AgentEvent, AgentSession, AgentTurnRequest
from app.services.execution import native_tools
from app.services.llm import stream_llm_with_tools
from app.services.tool_specs import TOOL_REGISTRY

logger = logging.getLogger(__name__)

MAX_NATIVE_STEPS = 30
MAX_CONTEXT_OBS_CHARS = 80_000

NATIVE_SYSTEM_PROMPT = """You are OmicsBase, an advanced autonomous bioinformatics and computational biology pair programmer.
You have direct, deterministic access to the workspace filesystem and terminal execution environment.

## Behavioral Protocol (Plan & Verify):
1. **Research First**: Inspect existing project files, data dictionaries in `data/`, and script headers before proposing or making changes.
2. **Deterministic Modifications**:
   - Use `replace_file_content` to make exact, targeted changes to existing files.
   - Use `write_to_file` only when creating new files or completely rewriting small configs.
3. **In-Loop Verification**:
   - When modifying or generating R scripts (`.R`), Quarto reports (`.qmd`), or shell scripts, run them using `run_command` (e.g. `Rscript script.R` or `quarto render ...`).
   - Inspect raw stdout/stderr to confirm 0 runtime exceptions or syntax errors before reporting completion.
4. **Bioinformatics Standards**:
   - Use Bioconductor best practices, tidyverse, MultiAssayExperiment, and SummarizedExperiment.
   - Maintain reproducible pipelines and clean data transformations.
"""


def _tool_summary(name: str, args: dict[str, Any]) -> str:
    if name == "view_file":
        p = args.get("path", "")
        s, e = args.get("start_line"), args.get("end_line")
        return f"View {p}:{s}-{e}" if s and e else f"View {p}"
    if name == "replace_file_content":
        return f"Edit {args.get('path', '')}"
    if name == "write_to_file":
        return f"Write {args.get('path', '')}"
    if name == "list_dir":
        return f"List {args.get('path', '.')}"
    if name == "grep_search":
        return f"Search '{args.get('query', '')}'"
    if name == "run_command":
        cmd = str(args.get("command", "")).strip()
        return f"Run: {cmd[:45]}..." if len(cmd) > 45 else f"Run: {cmd}"
    return name


class NativeCodingBackend:
    """Fast, deterministic 2-layer coding agent backend."""

    name = "native"

    async def _execute_native_tool(
        self,
        project_dir: Path,
        name: str,
        arguments: dict[str, Any],
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Dispatch native tool call directly against the project directory."""
        try:
            if name == "view_file":
                return native_tools.view_file(
                    project_dir,
                    path=arguments.get("path", ""),
                    start_line=arguments.get("start_line"),
                    end_line=arguments.get("end_line"),
                )
            elif name == "replace_file_content":
                return native_tools.replace_file_content(
                    project_dir,
                    path=arguments.get("path", ""),
                    target_content=arguments.get("target_content", ""),
                    replacement_content=arguments.get("replacement_content", ""),
                    allow_multiple=bool(arguments.get("allow_multiple", False)),
                )
            elif name == "write_to_file":
                return native_tools.write_to_file(
                    project_dir,
                    path=arguments.get("path", ""),
                    content=arguments.get("content", ""),
                    overwrite=bool(arguments.get("overwrite", True)),
                )
            elif name == "list_dir":
                return native_tools.list_dir(
                    project_dir,
                    path=arguments.get("path"),
                    recursive=bool(arguments.get("recursive", False)),
                )
            elif name == "grep_search":
                return native_tools.grep_search(
                    project_dir,
                    query=arguments.get("query", ""),
                    path=arguments.get("path"),
                    is_regex=bool(arguments.get("is_regex", False)),
                    case_insensitive=bool(arguments.get("case_insensitive", True)),
                )
            elif name == "run_command":
                return await native_tools.run_command(
                    project_dir,
                    command=arguments.get("command", ""),
                    timeout_seconds=int(arguments.get("timeout_seconds", 180)),
                    cancel_check=cancel_check,
                )
            else:
                return {"status": "error", "error": f"Unknown native tool: {name}"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    async def _stream(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        session_id = request.session.external_id if request.session else f"native-{uuid.uuid4().hex[:12]}"
        yield {"type": "session", "session_id": session_id}

        project_dir = request.project_dir
        tools = TOOL_REGISTRY.openai_tools(lens="workspace")

        # Build live workspace context
        live_context_parts = [
            f"Project Directory: {project_dir.resolve().as_posix()}",
        ]
        if request.plan:
            live_context_parts.append(f"## Current Plan:\n{request.plan}")
        if request.notes:
            live_context_parts.append(f"## Project Notes:\n{request.notes}")
        if request.selected_file:
            live_context_parts.append(f"## User Selected File:\n{request.selected_file}")
        if request.file_attachments:
            att_names = [
                f.get("original_name") or f.get("name")
                for f in request.file_attachments
                if isinstance(f, dict)
            ]
            if att_names:
                live_context_parts.append(
                    "## Attached Datasets (staged in data/):\n"
                    + "\n".join(f"- {name}" for name in att_names if name)
                )

        live_context = "\n\n".join(live_context_parts)
        system_prompt = NATIVE_SYSTEM_PROMPT

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": request.instruction}
        ]

        step = 0
        assistant_full_text = ""

        while step < MAX_NATIVE_STEPS:
            step += 1
            if request.cancel_check and request.cancel_check():
                yield {"type": "final", "message": "Turn cancelled by user."}
                return

            yield {"type": "step_started", "step": step}

            current_text_deltas: list[str] = []
            tool_calls_emitted: list[dict[str, Any]] = []

            async for event in stream_llm_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=4000,
                live_context=live_context,
                model_override=request.model,
                provider_override=request.provider,
            ):
                if request.cancel_check and request.cancel_check():
                    yield {"type": "final", "message": "Turn cancelled by user."}
                    return

                event_type = event.get("type")
                if event_type == "text_delta":
                    delta = event.get("content", "")
                    if delta:
                        current_text_deltas.append(delta)
                        assistant_full_text += delta
                        yield {"type": "token", "token": delta}
                elif event_type == "tool_call":
                    tool_calls_emitted.append(event)

            step_text = "".join(current_text_deltas)

            # If no tool calls, model finished its response
            if not tool_calls_emitted:
                yield {"type": "step_completed", "step": step}
                yield {"type": "final", "message": assistant_full_text or step_text}
                return

            # Append assistant turn with tool calls to conversation history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": step_text or None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }
                    for tc in tool_calls_emitted
                ],
            }
            messages.append(assistant_msg)

            # Execute tool calls
            for tc in tool_calls_emitted:
                call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                name = tc.get("name", "")
                args = tc.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                summary = _tool_summary(name, args)
                yield {
                    "type": "tool_started",
                    "tool": name,
                    "tool_call_id": call_id,
                    "reason": summary,
                    "step": step,
                }

                obs = await self._execute_native_tool(
                    project_dir,
                    name,
                    args,
                    cancel_check=request.cancel_check,
                )

                status = "ok" if obs.get("status") == "ok" else "error"
                yield {
                    "type": "tool_completed",
                    "tool": name,
                    "tool_call_id": call_id,
                    "status": status,
                    "summary": summary,
                    "observation": obs,
                    "step": step,
                }

                # If file modified, emit file update event for editor/UI
                if name in {"replace_file_content", "write_to_file"} and status == "ok":
                    p = args.get("path")
                    if p:
                        yield {"type": "file_updated", "path": p}

                obs_str = json.dumps(obs, default=str)
                if len(obs_str) > MAX_CONTEXT_OBS_CHARS:
                    obs_str = obs_str[:MAX_CONTEXT_OBS_CHARS] + '..."truncated": true}'

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": obs_str,
                })

            yield {"type": "step_completed", "step": step}

        yield {"type": "final", "message": assistant_full_text or "Completed maximum step limit."}

    def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        return self._stream(request)
