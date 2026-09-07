"""Stable contract between OmicsBase and workspace coding agents."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeAlias


AgentEvent: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class AgentSession:
    """A backend-qualified external session reference.

    The backend name is persisted alongside the vendor session identifier so a
    project cannot accidentally resume an OpenCode session with another
    adapter.
    """

    backend: str
    external_id: str


@dataclass(frozen=True)
class AgentTurnRequest:
    """Backend-independent inputs for one workspace agent turn."""

    project_dir: Path
    instruction: str
    provider: str | None = None
    model: str | None = None
    session: AgentSession | None = None
    fresh_session: bool = False
    chat_mode: str | None = None
    cancel_check: Callable[[], bool] | None = None
    question: str | None = None
    plan: str | None = None
    notes: str | None = None
    existing_sources: str | None = None
    attachments: str | None = None
    selected_file: str | None = None
    selected_content: str | None = None
    selected_content_dirty: bool = False
    preview_path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    file_attachments: list[dict[str, Any]] = field(default_factory=list)


class CodingAgentBackend(Protocol):
    """The minimal capability required by the workspace orchestration."""

    name: str

    def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[AgentEvent]:
        """Stream normalized workspace events for one agent turn."""

