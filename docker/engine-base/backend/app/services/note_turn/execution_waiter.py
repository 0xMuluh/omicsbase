"""Execution observation boundary for NoteThread turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def observe_execution(
    *,
    db: Any,
    execution_payload: dict,
    cell_payload: dict,
    timeout_seconds: int,
    turn_id: str,
    wait_enabled: bool,
    cancel_check: Callable[[], bool],
    wait_for_execution: Callable[..., Awaitable[dict]],
    observation_payload: Callable[..., dict],
) -> dict:
    """Wait for a queued execution once and shape its durable observation."""
    if (
        wait_enabled
        and str(execution_payload.get("status") or "")
        in {"queued", "running", "cancel_requested"}
    ):
        execution_payload = await wait_for_execution(
            db,
            str(execution_payload["id"]),
            timeout_seconds,
            cancel_check=cancel_check,
        )
    return observation_payload(execution_payload, cell_payload, turn_id=turn_id)
