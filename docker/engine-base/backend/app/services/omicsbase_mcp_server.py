"""OmicsBase MCP for the OpenCode workspace agent.

OpenCode already has bash/read/glob/edit. This server only exposes the
OmicsBase capabilities that need application state: clarification handling and
bounded, read-only Bioconductor knowledge retrieval. It does not duplicate
coding tools and does not require a structured analysis plan.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
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


MAX_KNOWLEDGE_QUERY_CHARS = 1_000
MAX_KNOWLEDGE_RESULTS = 8
MAX_KNOWLEDGE_RESPONSE_CHARS = 24_000


def _bounded_knowledge_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep retrieved guidance compact enough for an agent context window."""
    response = dict(result)
    original_matches = result.get("matches") or []
    bounded_matches: list[dict[str, Any]] = []
    remaining = MAX_KNOWLEDGE_RESPONSE_CHARS
    for raw_match in original_matches[:MAX_KNOWLEDGE_RESULTS]:
        if not isinstance(raw_match, dict):
            continue
        match = dict(raw_match)
        prose = str(match.get("prose") or "")
        code = str(match.get("code") or "")
        match_budget = min(6_000, remaining)
        prose_budget = min(len(prose), match_budget // 2)
        code_budget = min(len(code), match_budget - prose_budget)
        if prose_budget + code_budget < match_budget:
            extra = match_budget - prose_budget - code_budget
            if prose_budget < len(prose):
                take = min(extra, len(prose) - prose_budget)
                prose_budget += take
                extra -= take
            if extra and code_budget < len(code):
                code_budget += min(extra, len(code) - code_budget)
        if prose_budget < len(prose) or code_budget < len(code):
            response["truncated"] = True
        match["prose"] = prose[:prose_budget]
        match["code"] = code[:code_budget]
        bounded_matches.append(match)
        remaining -= prose_budget + code_budget
        if remaining <= 0:
            break
    response["matches"] = bounded_matches
    if len(bounded_matches) < len(original_matches):
        response["truncated"] = True
    response["response_limit_chars"] = MAX_KNOWLEDGE_RESPONSE_CHARS
    return response


MAX_KNOWLEDGE_REQUEST_SECONDS = 20
_ALLOWED_KNOWLEDGE_HOSTS = {"127.0.0.1", "localhost", "backend"}


def _knowledge_api_base_url() -> str:
    """Return a local backend URL, rejecting arbitrary network destinations."""
    configured = str(os.environ.get("OMICSBASE_API_URL") or "").strip().rstrip("/")
    project_dir = str(os.environ.get("OMICSBASE_PROJECT_DIR") or "").strip()
    # Existing Docker containers may have been created before the compose URL
    # was added. The mounted project path still identifies that deployment.
    default_url = (
        "http://backend:8000"
        if project_dir.startswith("/app/projects/")
        else "http://127.0.0.1:8000"
    )
    candidate = configured or default_url
    try:
        parsed = urllib.parse.urlsplit(candidate)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or 8000
    except ValueError:
        hostname, port, parsed = "", 0, None
    if (
        parsed is None
        or parsed.scheme != "http"
        or hostname not in _ALLOWED_KNOWLEDGE_HOSTS
        or port != 8000
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return "http://127.0.0.1:8000"
    return f"http://{hostname}:8000"


def _knowledge_api_search(
    query: str,
    channel: str,
    limit: int,
    book: str | None,
) -> dict[str, Any]:
    """Query the backend-owned index without opening its database to OpenCode."""
    params = {"q": query, "channel": channel, "limit": str(limit)}
    if book:
        params["book"] = book
    url = (
        f"{_knowledge_api_base_url()}/api/knowledge/search?"
        f"{urllib.parse.urlencode(params)}"
    )
    headers = {
        "Accept": "application/json",
        "X-Tenant-ID": (
            str(os.environ.get("OMICSBASE_TENANT_ID") or "default_tenant").strip()
            or "default_tenant"
        ),
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=MAX_KNOWLEDGE_REQUEST_SECONDS) as response:
            raw = response.read(MAX_KNOWLEDGE_RESPONSE_CHARS * 4)
        payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        return {
            "status": "error",
            "error": f"Knowledge API returned HTTP {exc.code}" + (f": {detail[:300]}" if detail else ""),
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            "status": "error",
            "error": f"Knowledge API unavailable: {str(exc)[:500]}",
        }
    if not isinstance(payload, dict):
        return {"status": "error", "error": "Knowledge API returned an invalid response"}
    return payload


def search_bioc_books(
    query: str,
    channel: str = "stable",
    limit: int = 4,
    book: str | None = None,
) -> dict[str, Any]:
    """Search the backend-owned, pinned Bioconductor QMD knowledge index."""
    normalised_query = " ".join(str(query or "").split())[:MAX_KNOWLEDGE_QUERY_CHARS]
    if not normalised_query:
        return {
            "status": "error",
            "error": "A non-empty scientific query is required",
            "matches": [],
        }
    normalised_channel = str(channel or "stable").strip().lower()
    if normalised_channel not in {"stable", "preview"}:
        return {
            "status": "error",
            "error": "channel must be stable or preview",
            "matches": [],
        }
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 4
    requested_limit = max(1, min(requested_limit, MAX_KNOWLEDGE_RESULTS))
    source_slug = str(book or "").strip()[:120] or None
    result = _knowledge_api_search(
        normalised_query,
        normalised_channel,
        requested_limit,
        source_slug,
    )
    return _bounded_knowledge_result(result)


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
    "search_bioc_books": {
        "description": (
            "Search the pinned QMD-derived Bioconductor knowledge base for "
            "standard omics approaches, package behavior, assumptions, "
            "interpretation, and worked examples. Use it whenever scientific "
            "grounding would improve the work; results guide the analysis but "
            "do not replace evidence from the project data."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The scientific or coding question to search.",
                    "maxLength": MAX_KNOWLEDGE_QUERY_CHARS,
                },
                "channel": {
                    "type": "string",
                    "enum": ["stable", "preview"],
                    "default": "stable",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_KNOWLEDGE_RESULTS,
                    "default": 4,
                },
                "book": {
                    "type": "string",
                    "description": "Optional curated book slug.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": search_bioc_books,
    },
}


# --- JSON-RPC / MCP stdio transport -------------------------------------

def _visible_tools() -> dict[str, dict[str, Any]]:
    """Apply the server scope without changing the shared tool registry."""
    scope = str(os.environ.get("OMICSBASE_MCP_SCOPE") or "all").strip().lower()
    if scope in {"knowledge", "workspace_knowledge"}:
        return {"search_bioc_books": _TOOLS["search_bioc_books"]}
    return _TOOLS


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
                    for name, spec in _visible_tools().items()
                ]
            },
        )
    if method == "tools/call":
        tool_name = str((params or {}).get("name") or "")
        spec = _visible_tools().get(tool_name)
        if spec is None:
            return _rpc(
                msg_id,
                error={"code": -32602, "message": f"Unknown or unavailable tool: {tool_name}"},
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
