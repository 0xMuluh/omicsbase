"""Coding-agent backend registry and streaming facade."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import os
from typing import Any

from app.config import settings

from .contract import AgentEvent, AgentSession, AgentTurnRequest, CodingAgentBackend
from .codex import CodexBackend
from .opencode import OpenCodeBackend
from .native import NativeCodingBackend


_BACKENDS: dict[str, CodingAgentBackend] = {
    "native": NativeCodingBackend(),
    "codex": CodexBackend(),
    "opencode": OpenCodeBackend(),
}


def _configured_backend_name() -> str:
    # CODING_AGENT_BACKEND is the primary explicit setting.
    value = str(os.getenv("CODING_AGENT_BACKEND", "") or "").strip()
    if not value:
        value = str(getattr(settings, "coding_agent_backend", "") or "").strip()
    if not value:
        value = str(getattr(settings, "agent_backend", "native") or "native").strip()
    return value.lower() or "native"


def register_coding_agent_backend(backend: CodingAgentBackend) -> None:
    """Register a backend implementation for dependency injection or plugins."""
    backend_name = str(getattr(backend, "name", "") or "").strip().lower()
    if not backend_name:
        raise ValueError("Coding-agent backends must define a non-empty name")
    _BACKENDS[backend_name] = backend


def get_coding_agent_backend(name: str | None = None) -> CodingAgentBackend:
    backend_name = (name or _configured_backend_name()).strip().lower()
    try:
        return _BACKENDS[backend_name]
    except KeyError as exc:
        available = ", ".join(sorted(_BACKENDS))
        raise ValueError(
            f"Unknown coding-agent backend {backend_name!r}; available backends: {available}"
        ) from exc


async def stream_coding_agent(
    *,
    project_dir: str | Path,
    instruction: str,
    provider: str | None = None,
    model: str | None = None,
    session: AgentSession | None = None,
    session_id: str | None = None,
    fresh_session: bool = False,
    chat_mode: str | None = None,
    cancel_check: Any = None,
    backend: str | None = None,
    question: str | None = None,
    plan: str | None = None,
    notes: str | None = None,
    existing_sources: str | None = None,
    attachments: str | None = None,
    selected_file: str | None = None,
    selected_content: str | None = None,
    selected_content_dirty: bool = False,
    preview_path: str | None = None,
    file_attachments: list[dict[str, Any]] | None = None,
    **options: Any,
) -> AsyncIterator[AgentEvent]:
    """Stream one turn through the configured backend.

    ``session_id`` remains accepted for callers migrating from the OpenCode
    function. New code should pass the qualified ``AgentSession`` instead.
    """
    selected = get_coding_agent_backend(backend)
    if session is None and session_id:
        session = AgentSession(backend=selected.name, external_id=session_id)
    if session is not None and session.backend != selected.name:
        raise ValueError(
            f"Session belongs to {session.backend!r}, but {selected.name!r} was selected"
        )
    request = AgentTurnRequest(
        project_dir=Path(project_dir),
        instruction=instruction,
        provider=provider,
        model=model,
        session=session,
        fresh_session=fresh_session,
        chat_mode=chat_mode,
        cancel_check=cancel_check,
        question=question,
        plan=plan,
        notes=notes,
        existing_sources=existing_sources,
        attachments=attachments,
        selected_file=selected_file,
        selected_content=selected_content,
        selected_content_dirty=selected_content_dirty,
        preview_path=preview_path,
        file_attachments=list(file_attachments or []),
        options=options,
    )
    async for event in selected.stream_turn(request):
        yield event

