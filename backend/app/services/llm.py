"""Configurable LLM client supporting Anthropic, OpenAI, Qwen, Gemini, OpenRouter, Groq, and xAI Grok."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.sanitizer import sanitize_text

logger = logging.getLogger(__name__)


_cached_system_prompt: str | None = None
_cached_prompt_mtimes: dict[str, tuple[int, int, str]] = {}

_anthropic_client: Any = None
_anthropic_key: str | None = None

_async_anthropic_client: Any = None
_async_anthropic_key: str | None = None

_openai_clients: dict[tuple[str, str | None], Any] = {}
_async_openai_clients: dict[tuple[str, str | None], Any] = {}


def _get_anthropic_client(api_key: str):
    global _anthropic_client, _anthropic_key
    import anthropic

    if _anthropic_client is None or _anthropic_key != api_key:
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        _anthropic_key = api_key
    return _anthropic_client


def _get_async_anthropic_client(api_key: str):
    global _async_anthropic_client, _async_anthropic_key
    import anthropic

    if _async_anthropic_client is None or _async_anthropic_key != api_key:
        _async_anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
        _async_anthropic_key = api_key
    return _async_anthropic_client


def _get_openai_client(api_key: str, base_url: str | None):
    from openai import OpenAI

    key = (api_key, base_url)
    if key not in _openai_clients:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        _openai_clients[key] = OpenAI(**client_kwargs)
    return _openai_clients[key]


def _get_async_openai_client(api_key: str, base_url: str | None):
    from openai import AsyncOpenAI

    key = (api_key, base_url)
    if key not in _async_openai_clients:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
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
) -> str:
    """Call the configured LLM and return response text."""
    system_prompt = sanitize_text(system_prompt)
    user_prompt = sanitize_text(user_prompt)

    provider = (provider_override or settings.llm_provider).lower()

    if provider == "anthropic":
        return await _call_anthropic(system_prompt, user_prompt, max_tokens, model_override=model_override)
    elif provider in {"openai", "qwen", "gemini", "openrouter", "deepseek", "groq", "grok", "xai", "ollama"}:
        return await _call_openai(
            system_prompt, user_prompt, response_format, max_tokens,
            provider=provider, model_override=model_override,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


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
    if provider == "anthropic":
        async for chunk in _stream_anthropic(system_prompt, user_prompt, max_tokens, model_override=model_override):
            yield chunk
        return
    if provider in {"openai", "qwen", "gemini", "openrouter", "deepseek", "groq", "grok", "xai", "ollama"}:
        async for chunk in _stream_openai(
            system_prompt, user_prompt, max_tokens, provider=provider, model_override=model_override,
        ):
            yield chunk
        return
    raise ValueError(f"Unknown LLM provider: {provider}")


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
    if provider == "anthropic":
        async for event in _stream_anthropic_with_tools(
            system_prompt, messages, tools, max_tokens, live_context=live_context, model_override=model_override,
        ):
            yield event
        return
    if provider in {"openai", "qwen", "gemini", "openrouter", "deepseek", "groq", "grok", "xai", "ollama"}:
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


async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    response_format: str,
    max_tokens: int,
    provider: str = "openai",
    model_override: str | None = None,
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
    if provider == "qwen":
        api_key = settings.dashscope_api_key or settings.qwen_api_key or settings.openai_api_key or "dummy-key"
        base_url = settings.qwen_base_url or settings.openai_base_url or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        model_name = settings.llm_model if settings.llm_model and "claude" not in settings.llm_model.lower() else "qwen-plus"
    elif provider == "gemini":
        api_key = settings.gemini_api_key or settings.openai_api_key or "dummy-key"
        base_url = settings.openai_base_url or "https://generativelanguage.googleapis.com/v1beta/openai/"
        model_name = settings.llm_model if settings.llm_model and "claude" not in settings.llm_model else "gemini-2.0-flash"
    elif provider == "openrouter":
        api_key = settings.openrouter_api_key or settings.openai_api_key or "dummy-key"
        base_url = settings.openai_base_url or "https://openrouter.ai/api/v1"
        model_name = settings.llm_model if settings.llm_model and "claude" not in settings.llm_model else "anthropic/claude-3.5-sonnet"
    elif provider == "groq":
        api_key = settings.groq_api_key or settings.openai_api_key or "dummy-key"
        base_url = settings.openai_base_url or "https://api.groq.com/openai/v1"
        model_name = settings.llm_model if settings.llm_model and "claude" not in settings.llm_model else "llama-3.3-70b-versatile"
    elif provider in {"grok", "xai"}:
        api_key = settings.grok_api_key or settings.xai_api_key or settings.openai_api_key or "dummy-key"
        base_url = settings.openai_base_url or "https://api.x.ai/v1"
        model_name = settings.llm_model if settings.llm_model and "claude" not in settings.llm_model else "grok-2-latest"
    else:
        api_key = settings.openai_api_key or "dummy-key"
        base_url = settings.openai_base_url
        model_name = settings.llm_model
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
    if provider == "openai":
        kwargs["stream_options"] = {"include_usage": True}
    _set_token_limit(kwargs, provider, model_name, max_tokens)

    stream = await client.chat.completions.create(**kwargs)

    # Accumulate tool calls across streamed chunks
    tool_calls_acc: dict[int, dict[str, Any]] = {}

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

            # Check for stop
            if chunk.choices[0].finish_reason:
                break

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

    # Groq intermittently hard-fails on a malformed tool-call JSON sample
    # ("Failed to call a function"); the next sample is usually valid.
    for attempt in range(2):
        try:
            async for event in _run_stream():
                yield event
            return
        except Exception as exc:
            if attempt == 0 and "failed to call a function" in str(exc).lower():
                logger.warning("Provider tool-call error, retrying once: %s", exc)
                continue
            raise


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
        system_blocks.append({"type": "text", "text": live_context})

    # Convert OpenAI tool format to Anthropic format
    anthropic_tools = []
    for tool in tools:
        func = tool.get("function", {})
        anthropic_tools.append({
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        })

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


def load_system_prompt() -> str:
    """Load and assemble the full system prompt with registry and writing guide."""
    global _cached_system_prompt, _cached_prompt_mtimes

    prompt_path = Path(settings.prompts_dir) / "system.md"
    registry_path = Path(settings.registry_path)
    guide_path = Path(settings.prompts_dir) / "REPORT_WRITING_GUIDE.md"

    tracked_paths = [prompt_path, registry_path, guide_path]
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
        parts.append(_load_prompt("system"))
    except FileNotFoundError:
        parts.append("You are a scientific microbiome analysis assistant.")

    if registry_path.exists():
        parts.append("\n\n## Decision-Point Registry\n\n```yaml\n" + registry_path.read_text() + "\n```")

    if guide_path.exists():
        parts.append("\n\n## Report Writing Guide\n\n" + guide_path.read_text())

    assembled = "\n".join(parts)
    _cached_system_prompt = assembled
    _cached_prompt_mtimes = current_mtimes
    return assembled

