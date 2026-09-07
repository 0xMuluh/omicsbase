"""Anthropic request and tool-streaming implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _compat():
    from app.services import llm
    return llm


def _get_async_anthropic_client(api_key: str):
    return _compat()._get_async_anthropic_client(api_key)


def _usage_payload(usage: Any) -> dict[str, int] | None:
    return _compat()._usage_payload(usage)


def _normalise_tool_arguments(arguments: Any) -> dict[str, Any]:
    return _compat()._normalise_tool_arguments(arguments)

async def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    model_override: str | None = None,
) -> str:
    """Call Anthropic Claude API with prompt caching."""
    client = _get_async_anthropic_client(settings.anthropic_api_key)
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    message = await client.messages.create(
        model=model_override or settings.llm_model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


async def _stream_anthropic(system_prompt: str, user_prompt: str, max_tokens: int, model_override: str | None = None):
    client = _get_async_anthropic_client(settings.anthropic_api_key)
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    async with client.messages.stream(
        model=model_override or settings.llm_model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        async for text in stream.text_stream:
            if text:
                yield text


def _convert_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-format tool definitions to Anthropic format.

    The tools block is constant within and across turns (~15-20k tokens for
    the workspace lens), so the last tool carries a cache breakpoint to keep
    the whole block cached after the first call.
    """
    anthropic_tools = []
    for index, tool in enumerate(tools):
        func = tool.get("function", {})
        converted = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        }
        if index == len(tools) - 1:
            converted["cache_control"] = {"type": "ephemeral"}
        anthropic_tools.append(converted)
    return anthropic_tools


async def _stream_anthropic_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    live_context: str | None = None,
    model_override: str | None = None,
):
    """Stream an Anthropic completion with native tool use."""
    client = _get_async_anthropic_client(settings.anthropic_api_key)
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if live_context:
        # Stable within a turn (and small with delta context), so cache it
        # too; caching is per-block, so a changed live_context keeps the
        # system prompt as a cache hit.
        system_blocks.append({
            "type": "text",
            "text": live_context,
            "cache_control": {"type": "ephemeral"},
        })

    # Convert OpenAI tool format to Anthropic format
    anthropic_tools = _convert_anthropic_tools(tools)

    # Convert messages to Anthropic format
    anthropic_messages = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            # Anthropic expects tool results as user messages with tool_result content
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", "unknown"),
                    "content": msg.get("content", ""),
                }],
            })
        elif role == "assistant" and msg.get("tool_calls"):
            # Assistant message with tool calls
            content: list[dict[str, Any]] = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id", "unknown"),
                    "name": tc.get("function", {}).get("name", ""),
                    "input": _normalise_tool_arguments(
                        tc.get("function", {}).get("arguments", {})
                    ),
                })
            anthropic_messages.append({"role": "assistant", "content": content})
        else:
            anthropic_messages.append({"role": role, "content": msg.get("content") or ""})

    async with client.messages.stream(
        model=model_override or settings.llm_model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=anthropic_messages,
        tools=anthropic_tools if anthropic_tools else None,
    ) as stream:
        current_tool: dict[str, Any] | None = None
        current_tool_json = ""

        async for event in stream:
            if event.type == "content_block_start":
                block = event.content_block
                if hasattr(block, "type") and block.type == "tool_use":
                    current_tool = {"id": block.id, "name": block.name}
                    current_tool_json = ""
            elif event.type == "content_block_delta":
                delta = event.delta
                if hasattr(delta, "type"):
                    if delta.type == "text_delta" and hasattr(delta, "text"):
                        yield {"type": "text_delta", "content": delta.text}
                    elif delta.type == "input_json_delta" and hasattr(delta, "partial_json"):
                        current_tool_json += delta.partial_json
            elif event.type == "content_block_stop":
                if current_tool is not None:
                    import json as _json
                    try:
                        args = _json.loads(current_tool_json) if current_tool_json else {}
                    except _json.JSONDecodeError:
                        args = {}
                    yield {
                        "type": "tool_call",
                        "id": current_tool["id"],
                        "name": current_tool["name"],
                        "arguments": args,
                    }
                    current_tool = None
                    current_tool_json = ""

        try:
            final_message = await stream.get_final_message()
            usage = _usage_payload(getattr(final_message, "usage", None))
            if usage:
                yield {"type": "usage", "usage": usage}
        except Exception:
            pass

    yield {"type": "done"}


