"""Native Gemini request and tool-streaming implementation."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.config import settings
from app.services.providers import default_model_for

logger = logging.getLogger(__name__)


def _compat():
    # Kept lazy so the compatibility façade can be imported without a cycle.
    from app.services import llm
    return llm


def _get_gemini_client(api_key: str):
    return _compat()._get_gemini_client(api_key)


def _resolve_gemini_model(model_override: str | None = None) -> str:
    return _compat()._resolve_gemini_model(model_override)


def _usage_payload(usage: Any) -> dict[str, int] | None:
    return _compat()._usage_payload(usage)

def _gemini_config(
    system_prompt: str,
    live_context: str | None,
    max_tokens: int,
    response_mime_type: str | None = None,
    tools: list[Any] | None = None,
    thinking_budget: int | None = None,
) -> Any:
    """Build a native GenerateContentConfig for the google-genai SDK."""
    from google.genai import types

    system_parts = [system_prompt]
    if live_context:
        system_parts.append(live_context)
    config = types.GenerateContentConfig(
        system_instruction="\n\n".join(system_parts),
        max_output_tokens=max_tokens,
    )
    if response_mime_type:
        config.response_mime_type = response_mime_type
    if tools:
        config.tools = tools
    budget = thinking_budget if thinking_budget is not None else settings.gemini_thinking_budget
    if budget and budget > 0:
        config.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    return config


def _clean_schema_for_gemini(schema: Any) -> Any:
    """Recursively adapt standard JSON Schema to Gemini's schema dialect.

    Gemini rejects unknown fields: additionalProperties/$schema/title are
    dropped, ``oneOf`` becomes the supported ``anyOf``, and ``const`` becomes
    a single-value ``enum``.
    """
    if isinstance(schema, dict):
        cleaned: dict[str, Any] = {}
        for k, v in schema.items():
            if k in ("additionalProperties", "additional_properties", "$schema", "title"):
                continue
            if k == "const":
                cleaned["enum"] = [v]
                continue
            if k == "oneOf":
                k = "anyOf"
            value = _clean_schema_for_gemini(v)
            if k == "anyOf" and isinstance(cleaned.get("anyOf"), list):
                cleaned["anyOf"] = cleaned["anyOf"] + value
            else:
                cleaned[k] = value
        return cleaned
    if isinstance(schema, list):
        return [_clean_schema_for_gemini(x) for x in schema]
    return schema


def _gemini_tools(tools: list[dict[str, Any]]) -> list[Any] | None:
    """Convert OpenAI-format tool definitions to Gemini FunctionDeclarations."""
    from google.genai import types

    if not tools:
        return None
    declarations = []
    for tool in tools:
        func = tool.get("function", {})
        declarations.append(
            types.FunctionDeclaration(
                name=func.get("name", ""),
                description=func.get("description", ""),
                parameters=_clean_schema_for_gemini(func.get("parameters", {"type": "object", "properties": {}})),
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _openai_to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[list[Any], dict[str, str]]:
    """Convert OpenAI-style messages to Gemini Content parts.

    Returns (contents, tool_id_to_name) where the map lets later tool
    responses resolve their function name from the preceding assistant call.
    """
    from google.genai import types

    contents: list[Any] = []
    tool_id_to_name: dict[str, str] = {}
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=msg.get("content") or "")]))
        elif role == "assistant":
            parts: list[Any] = []
            if msg.get("content"):
                parts.append(types.Part(text=msg["content"]))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                call_id = tc.get("id") or f"call_{len(tool_id_to_name)}"
                tool_id_to_name[call_id] = name
                arguments = fn.get("arguments") or "{}"
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                part = types.Part(
                    function_call=types.FunctionCall(id=call_id, name=name, args=arguments)
                )
                # Gemini 3 validates that functionCall parts echo back the
                # thought_signature they were generated with; without it the
                # API rejects the whole request (400 INVALID_ARGUMENT).
                signature = tc.get("thought_signature")
                if signature:
                    try:
                        part.thought_signature = base64.b64decode(signature)
                    except (ValueError, TypeError):
                        pass
                parts.append(part)
            contents.append(types.Content(role="model", parts=parts))
        elif role == "tool":
            name = tool_id_to_name.get(msg.get("tool_call_id") or "", "unknown")
            raw = msg.get("content") or ""
            try:
                response = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                response = {"output": raw}
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(name=name, response=response)
                        )
                    ],
                )
            )
    return contents, tool_id_to_name


async def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    response_format: str,
    max_tokens: int,
    model_override: str | None = None,
) -> str:
    """Call the Gemini native API (google-genai SDK)."""
    model = _resolve_gemini_model(model_override)
    client = _get_gemini_client(settings.gemini_api_key or settings.openai_api_key or "dummy-key")
    config = _gemini_config(
        system_prompt,
        None,
        max_tokens,
        response_mime_type="application/json" if response_format == "json" else None,
    )
    response = await client.aio.models.generate_content(
        model=model, contents=user_prompt, config=config
    )
    return (response.text if response is not None else None) or ""


async def _stream_gemini(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    model_override: str | None = None,
):
    """Stream text from the Gemini native API."""
    import inspect

    model = _resolve_gemini_model(model_override)
    client = _get_gemini_client(settings.gemini_api_key or settings.openai_api_key or "dummy-key")
    config = _gemini_config(system_prompt, None, max_tokens)
    stream_or_coro = client.aio.models.generate_content_stream(
        model=model, contents=user_prompt, config=config
    )
    stream = await stream_or_coro if inspect.isawaitable(stream_or_coro) else stream_or_coro
    async for chunk in stream:
        if chunk and chunk.text:
            yield chunk.text


async def _stream_gemini_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    live_context: str | None = None,
    model_override: str | None = None,
):
    """Stream from the Gemini native API with function calling.

    Yields the same event shapes as the OpenAI/Anthropic tool paths:
    text_delta, tool_call, usage, done. Function call arguments may arrive
    split across chunks, so they are accumulated before emitting.
    """
    import inspect

    model = _resolve_gemini_model(model_override)
    client = _get_gemini_client(settings.gemini_api_key or settings.openai_api_key or "dummy-key")
    contents, _ = _openai_to_gemini_contents(messages)
    config = _gemini_config(system_prompt, live_context, max_tokens, tools=_gemini_tools(tools))

    tool_calls_acc: dict[str, dict[str, Any]] = {}
    tool_call_order: list[str] = []
    usage_payload: dict[str, int] | None = None

    def _consume_chunk(chunk: Any):
        nonlocal usage_payload
        if chunk is None:
            return
        metadata = getattr(chunk, "usage_metadata", None)
        if metadata is not None:
            usage_payload = {
                "input_tokens": int(getattr(metadata, "prompt_token_count", 0) or 0),
                "output_tokens": int(getattr(metadata, "candidates_token_count", 0) or 0),
            }
        # Walk the raw parts (not chunk.function_calls) so each function
        # call's thought_signature can be captured for history round-trips.
        parts: list[Any] = []
        candidates = getattr(chunk, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = list(getattr(content, "parts", None) or [])
        if not any(getattr(p, "function_call", None) is not None for p in parts):
            if chunk.text:
                yield {"type": "text_delta", "content": chunk.text}
            return
        for part in parts:
            call = getattr(part, "function_call", None)
            if call is None:
                continue
            call_id = call.id or f"call_{len(tool_call_order)}"
            if call_id not in tool_calls_acc:
                tool_calls_acc[call_id] = {"id": call_id, "name": "", "args": {}}
                tool_call_order.append(call_id)
            acc = tool_calls_acc[call_id]
            if call.name:
                acc["name"] = call.name
            if call.args:
                acc["args"].update(call.args)
            signature = getattr(part, "thought_signature", None)
            if signature:
                acc["thought_signature"] = (
                    base64.b64encode(signature).decode("ascii")
                    if isinstance(signature, (bytes, bytearray))
                    else str(signature)
                )

    def _emit_tool_calls():
        for call_id in tool_call_order:
            tc = tool_calls_acc[call_id]
            event = {"type": "tool_call", "id": tc["id"], "name": tc["name"], "arguments": tc["args"]}
            if tc.get("thought_signature"):
                event["thought_signature"] = tc["thought_signature"]
            yield event

    for attempt in range(2):
        try:
            stream_or_coro = client.aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            )
            stream = await stream_or_coro if inspect.isawaitable(stream_or_coro) else stream_or_coro
            async for chunk in stream:
                for event in _consume_chunk(chunk):
                    yield event
            break
        except Exception as exc:
            if attempt == 0 and not tool_call_order and not tool_calls_acc:
                logger.warning("Gemini stream failed, retrying once: %s", exc)
                continue
            raise

    for event in _emit_tool_calls():
        yield event
    if usage_payload:
        yield {"type": "usage", "usage": usage_payload}
    yield {"type": "done"}


