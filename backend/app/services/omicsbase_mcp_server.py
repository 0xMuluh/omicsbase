"""OmicsBase MCP for the OpenCode workspace agent.

OpenCode already has bash/read/glob/edit. This server only exposes tools that
must touch OmicsBase app state — currently ask_user, so the Plan/workspace UI
can pause for a clarification. It does not duplicate coding tools and does
not require a structured analysis plan.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# --- project resolution -------------------------------------------------

def _project_dir() -> Path:
    configured = str(os.environ.get("OMICSBASE_PROJECT_DIR") or "").strip()
    if configured:
        return Path(configured).resolve()
    return Path.cwd().resolve()


def _load_project(db):
    """Find the Project row whose project_dir identifies the running project."""
    from app.models.project import Project

    base = _project_dir()
    project = (
        db.query(Project)
        .filter(Project.project_dir == str(base))
        .first()
    )
    if project is not None:
        return project
    prefix = str(base) + os.sep
    return (
        db.query(Project)
        .filter(Project.project_dir.like(f"{prefix}%"))
        .first()
    )


# --- contract tools -----------------------------------------------------

def ask_user(question: str, options: list[str] | None = None, multiple: bool = False) -> dict:
    """Ask the user a clarifying question with concrete options.

    Records the question as a pending clarification the Plan UI reads; the
    turn ends and waits for the user's answer before continuing.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        project = _load_project(db)
        if project is None:
            return {
                "status": "error",
                "error": f"No OmicsBase project found for {_project_dir()}",
            }
        memory = dict(project.agent_memory or {})
        memory["pending_clarifications"] = {
            "message": "The agent needs a decision before continuing.",
            "questions": [
                {
                    "id": "question-1",
                    "prompt": str(question)[:500],
                    "options": [str(option) for option in (options or [])][:8],
                    "multiple": bool(multiple),
                    "allow_custom": True,
                }
            ],
        }
        project.agent_memory = memory
        if str(project.status or "") in {"created", "generating", "rendering", "editing"}:
            project.status = "needs_clarification"
        db.commit()
        return {
            "status": "ok",
            "summary": "Clarification requested; the turn pauses for the user's answer.",
        }
    finally:
        db.close()


_TOOLS: dict[str, dict[str, Any]] = {
    "ask_user": {
        "description": "Ask the user a clarifying question with concrete options; the turn pauses for the answer.",
        "schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "multiple": {"type": "boolean", "default": False},
            },
            "required": ["question"],
        },
        "handler": ask_user,
    },
}


# --- JSON-RPC / MCP stdio transport -------------------------------------

def _rpc(id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _rpc(
            msg_id,
            {
                "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "omicsbase-contracts", "version": "1.0.0"},
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled", "notifications/progress"}:
        return None
    if method == "ping":
        return _rpc(msg_id, {})
    if method == "tools/list":
        return _rpc(
            msg_id,
            {
                "tools": [
                    {
                        "name": name,
                        "description": spec["description"],
                        "inputSchema": spec["schema"],
                    }
                    for name, spec in _TOOLS.items()
                ]
            },
        )
    if method == "tools/call":
        tool_name = str((params or {}).get("name") or "")
        spec = _TOOLS.get(tool_name)
        if spec is None:
            return _rpc(
                msg_id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"},
            )
        arguments = (params or {}).get("arguments") or {}
        try:
            result = spec["handler"](**arguments)
        except TypeError as exc:
            result = {"status": "error", "error": f"Invalid arguments for {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("OmicsBase MCP tool %s failed", tool_name)
            result = {"status": "error", "error": str(exc)}
        return _rpc(
            msg_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, sort_keys=True, default=str),
                    }
                ],
                "isError": bool(result.get("status") == "error"),
            },
        )
    return _rpc(
        msg_id,
        error={"code": -32601, "message": f"Method not found: {method}"},
    )


def run_stdio() -> None:
    """Serve MCP over stdio (newline-delimited JSON-RPC 2.0)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (TypeError, ValueError):
            continue
        response = _handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio()
