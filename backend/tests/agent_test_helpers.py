"""Small helpers for testing the native streaming tool protocol."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def stream_turns(turns: Iterable[list[dict[str, Any]]]):
    """Return a fake ``stream_llm_with_tools`` implementation.

    Each list is one provider response. Keeping the responses as native
    streaming events makes tests exercise the same tool-call boundaries as
    the production loop instead of the removed JSON decision protocol.
    """
    pending_turns = iter(turns)

    async def fake_stream(**_kwargs):
        for event in next(pending_turns):
            yield event

    return fake_stream


def text_turn(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "text_delta", "content": text},
        {"type": "done"},
    ]


def tool_turn(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str = "call-1",
) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_call",
            "id": call_id,
            "name": name,
            "arguments": arguments or {},
        },
        {"type": "done"},
    ]


def failing_stream(message: str = "provider unavailable"):
    """Return a native async stream that fails when consumed."""

    async def fake_stream(**_kwargs):
        raise RuntimeError(message)
        if False:  # pragma: no cover - makes this an async generator
            yield {"type": "done"}

    return fake_stream
