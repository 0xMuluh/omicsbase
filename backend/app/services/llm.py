"""Configurable LLM client supporting Anthropic, OpenAI, Qwen, Gemini, OpenRouter, OrcaRouter, Groq, and xAI Grok."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.providers import api_key_for, base_url_for, default_model_for, is_openai_compat
from app.services.provider_errors import raise_classified_provider_exception
from app.services.sanitizer import sanitize_text
from app.services.prompt_rules import inspect_prompt, inspect_system_prompt, prompt_fingerprint

logger = logging.getLogger(__name__)


_cached_system_prompt: str | None = None
_cached_prompt_mtimes: dict[str, tuple[int, int, str]] = {}

_anthropic_client: Any = None
_anthropic_key: str | None = None

_async_anthropic_client: Any = None
_async_anthropic_key: str | None = None

_openai_clients: dict[tuple[str, str | None], Any] = {}
_async_openai_clients: dict[tuple[str, str | None], Any] = {}

_gemini_client: Any = None
_gemini_client_key: str | None = None

# Stable fallback when the configured model isn't a Gemini model name.
_GEMINI_FALLBACK_MODEL = "gemini-3.6-flash"
_NON_GEMINI_HINTS = ("claude", "gpt-", "deepseek", "llama", "qwen", "grok", "o1-", "o3-", "o4-", "grok-")


def _get_gemini_client(api_key: str):
    global _gemini_client, _gemini_client_key
    from google import genai

    if _gemini_client is None or _gemini_client_key != api_key:
        _gemini_client = genai.Client(api_key=api_key)
        _gemini_client_key = api_key
    return _gemini_client


def _resolve_gemini_model(model_override: str | None = None) -> str:
    """Return the Gemini model name, guarding against non-Gemini leftovers."""
    model = (model_override or settings.llm_model or "").strip()
    if not model or any(hint in model.lower() for hint in _NON_GEMINI_HINTS):
        return default_model_for("gemini") or _GEMINI_FALLBACK_MODEL
    return model


def _get_anthropic_client(api_key: str):
    global _anthropic_client, _anthropic_key
    import anthropic

    if _anthropic_client is None or _anthropic_key != api_key:
        _anthropic_client = anthropic.Anthropic(api_key=api_key, timeout=_request_timeout_seconds())
        _anthropic_key = api_key
    return _anthropic_client


def _get_async_anthropic_client(api_key: str):
    global _async_anthropic_client, _async_anthropic_key
    import anthropic

    if _async_anthropic_client is None or _async_anthropic_key != api_key:
        _async_anthropic_client = anthropic.AsyncAnthropic(api_key=api_key, timeout=_request_timeout_seconds())
        _async_anthropic_key = api_key
    return _async_anthropic_client


def _request_timeout_seconds() -> float:
    """Cap any single provider request so stalls fail fast, not after 10+ minutes."""
    try:
        return float(max(120, int(settings.agent_run_stale_after_seconds or 300)))
    except (TypeError, ValueError):
        return 300.0


def _get_openai_client(api_key: str, base_url: str | None):
    from openai import OpenAI

    key = (api_key, base_url)
    if key not in _openai_clients:
        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": _request_timeout_seconds()}
        if base_url:
            client_kwargs["base_url"] = base_url
            if "dashscope" in base_url.lower() or "aliyun" in base_url.lower():
                client_kwargs["default_headers"] = {"x-dashscope-session-cache": "enable"}
        _openai_clients[key] = OpenAI(**client_kwargs)
    return _openai_clients[key]


def _get_async_openai_client(api_key: str, base_url: str | None):
    from openai import AsyncOpenAI

    key = (api_key, base_url)
    if key not in _async_openai_clients:
        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": _request_timeout_seconds()}
        if base_url:
            client_kwargs["base_url"] = base_url
            if "dashscope" in base_url.lower() or "aliyun" in base_url.lower():
                client_kwargs["default_headers"] = {"x-dashscope-session-cache": "enable"}
        _async_openai_clients[key] = AsyncOpenAI(**client_kwargs)
    return _async_openai_clients[key]


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    path = Path(settings.prompts_dir) / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text()


def resolve_target(role: str) -> tuple[str | None, str | None]:
    """Return (provider, model) for a task role, or (None, None) to use globals."""
    targets = {
        "agent": settings.llm_agent_target,
        "fast": settings.llm_fast_target,
        "planner": settings.llm_planner_target,
        "title": settings.llm_title_target,
    }
    raw = (targets.get(role) or "").strip()
    if not raw:
        return None, None
    provider, _, model = raw.partition(":")
    return (provider.strip() or None), (model.strip() or None)


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    response_format: str = "text",
    max_tokens: int = 16000,
    model_override: str | None = None,
    provider_override: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Call the configured LLM and return response text."""
    system_prompt = sanitize_text(system_prompt)
    user_prompt = sanitize_text(user_prompt)

    provider = (provider_override or settings.llm_provider).lower()

    try:
        if provider == "gemini" and settings.gemini_native:
            return await _call_gemini(
                system_prompt, user_prompt, response_format, max_tokens, model_override=model_override
            )
        if provider == "anthropic":
            return await _call_anthropic(system_prompt, user_prompt, max_tokens, model_override=model_override)
        if is_openai_compat(provider):
            return await _call_openai(
                system_prompt, user_prompt, response_format, max_tokens,
                provider=provider, model_override=model_override, reasoning_effort=reasoning_effort,
            )
        raise ValueError(f"Unknown LLM provider: {provider}")
    except Exception as exc:
        raise_classified_provider_exception(exc, provider)


async def stream_llm_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    model_override: str | None = None,
    provider_override: str | None = None,
):
    """Yield text tokens from the configured LLM for conversational replies."""
    system_prompt = sanitize_text(system_prompt)
    user_prompt = sanitize_text(user_prompt)

    provider = (provider_override or settings.llm_provider).lower()
    try:
        if provider == "gemini" and settings.gemini_native:
            async for chunk in _stream_gemini(system_prompt, user_prompt, max_tokens, model_override=model_override):
                yield chunk
            return
        if provider == "anthropic":
            async for chunk in _stream_anthropic(system_prompt, user_prompt, max_tokens, model_override=model_override):
                yield chunk
            return
        if is_openai_compat(provider):
            async for chunk in _stream_openai(
                system_prompt, user_prompt, max_tokens, provider=provider, model_override=model_override,
            ):
                yield chunk
            return
        raise ValueError(f"Unknown LLM provider: {provider}")
    except Exception as exc:
        raise_classified_provider_exception(exc, provider)


async def stream_llm_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 4000,
    live_context: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
):
    """Yield streaming events with native function/tool calling.

    Yields dicts:
      {"type": "text_delta", "content": "..."}
      {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}
      {"type": "done"}
    """
    system_prompt = sanitize_text(system_prompt)
    live_context = sanitize_text(live_context) if live_context else None

    provider = (provider_override or settings.llm_provider).lower()
    try:
        if provider == "gemini" and settings.gemini_native:
            async for event in _stream_gemini_with_tools(
                system_prompt,
                messages,
                tools,
                max_tokens,
                live_context=live_context,
                model_override=model_override,
            ):
                yield event
            return
        if provider == "anthropic":
            async for event in _stream_anthropic_with_tools(
                system_prompt, messages, tools, max_tokens, live_context=live_context, model_override=model_override,
            ):
                yield event
            return
        if is_openai_compat(provider):
            async for event in _stream_openai_with_tools(
                system_prompt,
                messages,
                tools,
                max_tokens,
                provider=provider,
                live_context=live_context,
                model_override=model_override,
            ):
                yield event
            return
        raise ValueError(f"Unknown LLM provider: {provider}")
    except Exception as exc:
        raise_classified_provider_exception(exc, provider)


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


async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    response_format: str,
    max_tokens: int,
    provider: str = "openai",
    model_override: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Call OpenAI or OpenAI-compatible API (Qwen, Gemini, OpenRouter, Groq, xAI Grok, DeepSeek, Ollama)."""
    api_key, base_url, model_name = _resolve_openai_provider(provider, model_override)
    client = _get_async_openai_client(api_key, base_url)

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    _set_token_limit(kwargs, provider, model_name, max_tokens)
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}
    if reasoning_effort and _supports_reasoning_effort(provider, model_name):
        kwargs["reasoning_effort"] = reasoning_effort

    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as exc:
        if not _should_retry_with_alternate_token_param(exc, kwargs):
            raise
        response = await client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


async def _stream_openai(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    provider: str = "openai",
    model_override: str | None = None,
):
    api_key, base_url, model_name = _resolve_openai_provider(provider, model_override)
    client = _get_async_openai_client(api_key, base_url)
    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
    }
    _set_token_limit(kwargs, provider, model_name, max_tokens)
    stream = await client.chat.completions.create(**kwargs)
    async for event in stream:
        delta = event.choices[0].delta.content if event.choices else None
        if delta:
            yield delta


def _resolve_openai_provider(provider: str, model_override: str | None = None) -> tuple[str, str | None, str]:
    """Return (api_key, base_url, model_name) for an OpenAI-compatible provider."""
    api_key = api_key_for(provider) or "dummy-key"
    base_url = base_url_for(provider)
    model_name = default_model_for(provider, settings.llm_model)
    if model_override:
        model_name = model_override
    return api_key, base_url or None, model_name


def _normalise_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Convert tool arguments to the object shape expected by Anthropic."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalise_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy messages without dropping tool-call protocol fields.

    OpenAI-compatible APIs require the assistant ``tool_calls`` message to be
    followed by tool messages carrying the matching ``tool_call_id``. Rebuilding
    messages from only ``role`` and ``content`` breaks that association and can
    make an otherwise valid multi-step tool loop fail.
    """
    normalised: list[dict[str, Any]] = []
    for message in messages:
        copied_message = dict(message)
        tool_calls = copied_message.get("tool_calls")
        if isinstance(tool_calls, list):
            copied_tool_calls: list[dict[str, Any]] = []
            for tool_call in tool_calls:
                copied_tool_call = dict(tool_call)
                # Gemini-only bookkeeping; strict OpenAI-compat APIs reject
                # unknown tool_call fields.
                copied_tool_call.pop("thought_signature", None)
                function = copied_tool_call.get("function")
                if isinstance(function, dict):
                    copied_function = dict(function)
                    arguments = copied_function.get("arguments")
                    if isinstance(arguments, (dict, list)):
                        copied_function["arguments"] = json.dumps(arguments)
                    copied_tool_call["function"] = copied_function
                copied_tool_calls.append(copied_tool_call)
            copied_message["tool_calls"] = copied_tool_calls
        normalised.append(copied_message)
    return normalised


def _usage_payload(usage: Any) -> dict[str, int] | None:
    """Normalize provider usage objects without coupling the agent loop to an SDK."""
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif hasattr(usage, "dict"):
        usage = usage.dict()
    if not isinstance(usage, dict):
        return None
    normalized: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
        "cache_read_input_tokens": ("cache_read_input_tokens",),
        "cache_creation_input_tokens": ("cache_creation_input_tokens",),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = usage.get(key)
            if value is not None:
                try:
                    normalized[target] = int(value)
                except (TypeError, ValueError):
                    pass
                break
    return normalized or None


async def _stream_openai_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    provider: str = "openai",
    live_context: str | None = None,
    model_override: str | None = None,
):
    """Stream an OpenAI-compatible completion with native function calling."""
    api_key, base_url, model_name = _resolve_openai_provider(provider, model_override)
    client = _get_async_openai_client(api_key, base_url)

    # Keep the stable system prompt as the first prefix so providers can cache it.
    # OpenAI-compat caching is automatic per provider (OpenAI auto prompt
    # caching on gpt-5.x; DeepSeek auto context caching; Groq none) and keys
    # on the input prefix — so the ordering invariant is: stable system
    # prompt first, then the (usually unchanged) live_context, then history.
    # The delta-context placeholder (UNCHANGED_CONTEXT) is a constant, so
    # steps 2+ of a turn share one cached prefix.
    api_messages = [{"role": "system", "content": system_prompt}]
    if live_context:
        api_messages.append({"role": "system", "content": live_context})
    api_messages.extend(_normalise_openai_messages(messages))

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": api_messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
    if provider != "ollama":
        # Every OpenAI-compatible endpoint we route honours stream_options;
        # without it Qwen/DashScope turns report zero usage and cost
        # accounting falls back to output-character estimates.
        kwargs["stream_options"] = {"include_usage": True}
    if provider == "qwen" or "dashscope" in (base_url or "").lower():
        kwargs["extra_headers"] = {"x-dashscope-session-cache": "enable"}
    if tools and _supports_reasoning_effort(provider, model_name):
        # gpt-5.x on /v1/chat/completions rejects function tools unless
        # reasoning_effort is explicitly "none" (the API's own guidance);
        # without it, tool-loop turns fail with HTTP 400.
        kwargs["reasoning_effort"] = "none"
    _set_token_limit(kwargs, provider, model_name, max_tokens)

    async def _run_stream():
        stream = await client.chat.completions.create(**kwargs)

        # Accumulate tool calls across streamed chunks
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            usage = _usage_payload(getattr(chunk, "usage", None))
            if usage:
                yield {"type": "usage", "usage": usage}
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                yield {"type": "text_delta", "content": delta.content}

            # Tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or f"call_{idx}",
                            "name": "",
                            "arguments_json": "",
                        }
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["arguments_json"] += tc_delta.function.arguments

            # Do not stop on finish_reason: OpenAI sends the include_usage
            # chunk after the final choice chunk, with an empty choices list.
            # The async stream itself is the authoritative end boundary.

        # Emit accumulated tool calls
        import json as _json
        for _idx in sorted(tool_calls_acc):
            tc = tool_calls_acc[_idx]
            try:
                args = _json.loads(tc["arguments_json"]) if tc["arguments_json"] else {}
            except _json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "arguments": args}

        yield {"type": "done"}

    # One retry whenever the stream dies before producing any output: a
    # stalled connection, a Groq tool-call JSON failure, or a transient 5xx
    # all recover on the second attempt, and no partial output is lost
    # because nothing was yielded yet.
    for attempt in range(2):
        yielded = False
        try:
            async for event in _run_stream():
                yielded = True
                yield event
            return
        except Exception as exc:
            if attempt == 0 and not yielded:
                logger.warning("Provider stream failed before any output, retrying once: %s", exc)
                continue
            raise


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


def _set_token_limit(kwargs: dict[str, Any], provider: str, model_name: str, max_tokens: int) -> None:
    """Set the output token parameter expected by the selected API/model."""
    model = model_name.lower()
    if provider == "openai" and model.startswith(("gpt-5", "o1", "o3", "o4")):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens


def _supports_reasoning_effort(provider: str, model_name: str) -> bool:
    """Whether the resolved model accepts a reasoning_effort parameter."""
    model = (model_name or "").lower()
    return provider == "openai" and model.startswith(("gpt-5", "o1", "o3", "o4"))


def _should_retry_with_alternate_token_param(exc: Exception, kwargs: dict[str, Any]) -> bool:
    """Retry once when an OpenAI-compatible API rejects one token limit spelling."""
    message = str(exc)
    if "Unsupported parameter" not in message:
        return False
    if "'max_tokens'" in message and "max_tokens" in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        return True
    if "'max_completion_tokens'" in message and "max_completion_tokens" in kwargs:
        kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
        return True
    return False


def load_system_prompt(
    prompt_references: tuple[str, ...] | list[str] | None = None,
    *,
    include_registry: bool = True,
) -> str:
    """Load the base prompt plus only the scientific references in scope.

    The report generator passes references declared by its active ReportPack.
    Other callers receive the generic writing guide, never a hard-coded
    domain architecture.
    """
    global _cached_system_prompt, _cached_prompt_mtimes

    prompt_path = Path(settings.prompts_dir) / "system.md"
    registry_path = Path(settings.registry_path)
    skills_root = Path(
        settings.skills_dir
        or str(Path(settings.prompts_dir).resolve().parent / "skills")
    ).resolve()
    guide_path = (
        skills_root
        / "quarto-research-report"
        / "references"
        / "writing-style.md"
    )
    reference_paths: list[tuple[str, Path]] = []
    seen_paths: set[Path] = set()
    for reference in prompt_references or ():
        relative = Path(str(reference))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe scientific prompt reference: {reference!r}")
        candidate = (skills_root / relative).resolve()
        try:
            candidate.relative_to(skills_root)
        except ValueError as exc:
            raise ValueError(
                f"Scientific prompt reference escaped the skills root: {reference!r}"
            ) from exc
        if not candidate.is_file():
            raise FileNotFoundError(
                f"Scientific prompt reference not found: {reference!r}"
            )
        if candidate not in seen_paths:
            reference_paths.append((relative.as_posix(), candidate))
            seen_paths.add(candidate)

    tracked_paths = [prompt_path, guide_path, *(path for _, path in reference_paths)]
    if include_registry:
        tracked_paths.append(registry_path)
    current_mtimes: dict[str, tuple[int, int, str]] = {}
    for path in tracked_paths:
        if path.exists():
            stat = path.stat()
            current_mtimes[str(path)] = (
                getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
                int(stat.st_size),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

    if _cached_system_prompt is not None and current_mtimes == _cached_prompt_mtimes:
        return _cached_system_prompt

    parts = []
    try:
        base_prompt = _load_prompt("system")
    except FileNotFoundError:
        base_prompt = "You are a scientific omics analysis assistant."
    parts.append(base_prompt)

    system_prompt_issues = inspect_system_prompt(base_prompt)
    if system_prompt_issues["forbidden"]:
        logger.warning(
            "Global system prompt contains implementation-specific rules: %s",
            system_prompt_issues,
        )

    if include_registry and registry_path.exists():
        parts.append("\n\n## Decision-Point Registry\n\n```yaml\n" + registry_path.read_text() + "\n```")

    if guide_path.exists():
        parts.append("\n\n## Report Writing Guide\n\n" + guide_path.read_text())

    for reference, path in reference_paths:
        parts.append(
            f"\n\n## ReportPack Scientific Reference: {reference}\n\n"
            + path.read_text()
        )

    assembled = "\n".join(parts)
    prompt_issues = inspect_prompt(assembled)
    if prompt_issues["missing"] or prompt_issues["forbidden"]:
        logger.warning("System prompt executable-rule check reported: %s", prompt_issues)
    logger.debug("Loaded system prompt fingerprint=%s", prompt_fingerprint(assembled))
    _cached_system_prompt = assembled
    _cached_prompt_mtimes = current_mtimes
    return assembled
