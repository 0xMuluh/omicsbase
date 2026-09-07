"""OpenCode session lifecycle and event-shaping helpers."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.services.opencode_config import (
    _active_provider_id,
    clear_opencode_session,
    load_opencode_session,
    opencode_runtime_config,
    save_opencode_session,
)

_STDOUT_CHUNK = 64 * 1024
_CONFIGURED_DIRECTORIES: set[tuple[str, str, str]] = set()


async def iter_stdout_lines(stream: asyncio.StreamReader):
    """Yield newline-delimited stdout without asyncio's 64KiB readline cap."""
    buffer = b""
    while True:
        chunk = await stream.read(_STDOUT_CHUNK)
        if not chunk:
            break
        buffer += chunk
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line, buffer = buffer[:newline], buffer[newline + 1:]
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                yield text
    leftover = buffer.decode("utf-8", errors="replace").strip()
    if leftover:
        yield leftover


async def load_messages(client: Any, session_id: str, directory: str) -> list[dict[str, Any]]:
    """Fetch the full message list for a session; empty list when unavailable."""
    try:
        response = await client.get(f"/session/{session_id}/message", params={"directory": directory})
    except Exception:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    return [item for item in payload if isinstance(item, dict)]


def message_info(item: dict[str, Any]) -> dict[str, Any]:
    info = item.get("info")
    return info if isinstance(info, dict) else {}


async def load_message_roles(client: Any, session_id: str, directory: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for item in await load_messages(client, session_id, directory):
        info = message_info(item)
        message_id = str(info.get("id") or "").strip()
        role = str(info.get("role") or "").strip()
        if message_id and role:
            roles[message_id] = role
    return roles


def part_text_delta(part: dict[str, Any], seen: dict[str, str]) -> str:
    part_id = str(part.get("id") or "")
    full_text = str(part.get("text") or "")
    previous = seen.get(part_id, "")
    delta = full_text[len(previous):] if full_text.startswith(previous) else full_text
    if part_id:
        seen[part_id] = full_text
    return delta


async def ensure_project_runtime(
    client: Any,
    project_dir: Path,
    *,
    model_spec: str,
    provider: str | None,
) -> None:
    directory = str(project_dir.resolve())
    active = _active_provider_id(provider)
    cache_key = (directory, model_spec, active)
    if cache_key in _CONFIGURED_DIRECTORIES:
        return

    runtime = json.loads(opencode_runtime_config(project_dir, model_spec=model_spec, provider=provider))
    config_doc = {
        "model": runtime.get("model"),
        "disabled_providers": runtime.get("disabled_providers") or [],
    }
    if runtime.get("provider"):
        config_doc["provider"] = runtime["provider"]
    config_dir = Path(directory) / ".opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "opencode.json").write_text(json.dumps(config_doc, indent=2), encoding="utf-8")

    mcp_entry = (runtime.get("mcp") or {}).get("omicsbase")
    if mcp_entry:
        response = await client.post("/mcp", params={"directory": directory}, json={"name": "omicsbase", "config": mcp_entry})
        response.raise_for_status()
    _CONFIGURED_DIRECTORIES.add(cache_key)


def looks_like_project_uuid(session_id: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        session_id.strip(),
    ))


def looks_like_opencode_session(session_id: str) -> bool:
    value = session_id.strip()
    return value.startswith("ses") or (len(value) >= 16 and not looks_like_project_uuid(value))


async def ensure_session(
    client: Any,
    project_dir: Path,
    session_id: str | None,
    *,
    fresh: bool = False,
) -> str:
    directory = str(project_dir.resolve())
    if fresh:
        clear_opencode_session(project_dir)
    wanted = None if fresh else ((session_id or "").strip() or load_opencode_session(project_dir))
    if wanted and looks_like_opencode_session(wanted):
        response = await client.get(f"/session/{wanted}", params={"directory": directory})
        if response.status_code == 200:
            return wanted
        clear_opencode_session(project_dir)

    response = await client.post("/session", params={"directory": directory}, json={})
    response.raise_for_status()
    created = response.json()
    new_id = str(created.get("id") or "").strip()
    if not new_id:
        raise RuntimeError("OpenCode did not return a session id")
    save_opencode_session(project_dir, new_id)
    return new_id


def opencode_status_is_active(status: dict[str, Any]) -> bool:
    return str(status.get("type") or "") in {"busy", "retry"}


def permission_id_from_event(properties: dict[str, Any]) -> str | None:
    for key in ("permissionID", "permissionId", "id"):
        value = properties.get(key)
        if isinstance(value, str) and value.startswith("per"):
            return value
    permission = properties.get("permission")
    if isinstance(permission, dict):
        value = permission.get("id")
        if isinstance(value, str) and value.startswith("per"):
            return value
    return None


def map_part_event(part: dict[str, Any], step_counter: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    part_type = str(part.get("type") or "")
    if part_type == "text":
        text = str(part.get("text") or "")
        if text:
            events.append({"type": "token", "token": text})
        return events
    if part_type == "tool":
        tool_name = str(part.get("tool") or "tool")
        tool_part_id = str(part.get("id") or part.get("callID") or f"{tool_name}-{step_counter}")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        tool_output = state.get("output") or ""
        summary = state.get("title") or f"{tool_name} executed"
        tool_status = str(state.get("status") or "")
        events.append({"type": "tool_started", "tool": tool_name, "tool_call_id": tool_part_id, "reason": summary, "step": step_counter, "input": tool_input})
        if tool_status in {"completed", "error", "failed"}:
            events.append({
                "type": "action_event",
                "event": {
                    "id": part.get("id") or f"tool-{step_counter}",
                    "kind": "action",
                    "status": "ok" if tool_status == "completed" else "error",
                    "title": tool_name,
                    "summary": summary,
                    "tool_call_id": tool_part_id,
                    "target": {"tool": tool_name},
                    "output": str(tool_output)[:1000],
                },
            })
        return events
    if part_type == "step-finish":
        tokens = part.get("tokens")
        cost = part.get("cost")
        if tokens or cost is not None:
            events.append({"type": "step_completed", "step": step_counter, "tokens": tokens, "cost": cost})
    return events


def is_missing_session_error(stderr: str) -> bool:
    text = stderr.lower()
    return "session not found" in text or "resource not found" in text


__all__ = [
    "ensure_project_runtime",
    "ensure_session",
    "is_missing_session_error",
    "iter_stdout_lines",
    "load_message_roles",
    "load_messages",
    "looks_like_opencode_session",
    "looks_like_project_uuid",
    "map_part_event",
    "message_info",
    "opencode_status_is_active",
    "part_text_delta",
    "permission_id_from_event",
]
