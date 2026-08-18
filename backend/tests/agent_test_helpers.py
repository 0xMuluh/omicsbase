"""Small helpers for testing the native streaming tool protocol."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def stream_turns(turns: Iterable[list[dict[str, Any]]]):
    """Return a fake ``native provider event stream`` implementation.

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


def openhands_from_stream(native_stream):
    """Adapt a scripted native event stream to the OpenHands runtime boundary.

    Tests patch the OpenHands boundary rather than the deleted application
    loop. The production adapter is covered separately with real SDK events.
    """

    async def fake_runtime(executor, message, *, cancel_check=None):
        initial, handled = executor.initial_events(message)
        for event in initial:
            yield event
        if handled:
            return

        messages = executor.build_messages(message)
        live_context = executor.build_live_context()
        for step in range(1, int(getattr(executor, "max_steps", 8) or 8) + 1):
            calls = []
            text = []
            async for event in native_stream(
                system_prompt=getattr(executor, "system_prompt", ""),
                messages=messages,
                tools=getattr(executor, "tools", []),
                max_tokens=getattr(executor, "max_tokens", 4000),
                live_context=live_context,
                model_override=getattr(executor, "llm_model_override", None),
                provider_override=getattr(executor, "llm_provider_override", None),
            ):
                if event.get("type") == "usage":
                    yield {"type": "usage", "usage": event.get("usage") or {}}
                elif event.get("type") == "text_delta":
                    token = str(event.get("content") or "")
                    text.append(token)
                    yield {"type": "token", "token": token}
                elif event.get("type") == "tool_call":
                    calls.append(event)

            if not calls:
                yield executor.final_event("".join(text).strip() or executor.default_final_message)
                return

            for call in calls:
                arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                result = await executor.execute_tool(
                    str(call.get("name") or ""),
                    arguments,
                    step=step,
                    tool_call_id=str(call.get("id") or "test-call"),
                    persisted_arguments=arguments,
                    step_text="".join(text),
                )
                for emitted in result.events:
                    yield emitted
                if result.emit_completed:
                    yield executor.tool_completed_event(
                        str(call.get("name") or ""),
                        str(call.get("id") or "test-call"),
                        arguments,
                        "error" if str((result.observation or {}).get("status") or "").lower() == "error" else str((result.observation or {}).get("status") or "ok"),
                        str(result.summary or (result.observation or {}).get("status") or "ok"),
                        step,
                    )
                if result.wait_for:
                    yield {"type": "wait", "dependency": result.wait_for, "step": step}
                    if result.final_event is not None:
                        yield result.final_event
                    return
                if result.end_turn:
                    if result.final_event is not None:
                        yield result.final_event
                    return
                messages.append({"role": "assistant", "content": "".join(text) or None})
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or "test-call"),
                    "content": str(result.observation),
                })
                text = []
                live_context = executor.build_live_context()

        yield executor.final_event(getattr(executor, "max_steps_message", executor.default_final_message))

    return fake_runtime
