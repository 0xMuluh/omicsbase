"""OpenAI-compatible request and tool-streaming implementation."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings
from app.services.providers import api_key_for, base_url_for, default_model_for

logger = logging.getLogger(__name__)


def _compat():
    from app.services import llm
    return llm


def _get_async_openai_client(api_key: str, base_url: str | None):
    return _compat()._get_async_openai_client(api_key, base_url)


def _request_timeout_seconds() -> float:
    return _compat()._request_timeout_seconds()

async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    response_format: str,
    max_tokens: int,
    provider: str = "openai",
    model_override: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Call OpenAI or OpenAI-compatible API (Qwen, Gemini, OpenRouter, GMI Cloud, Groq, xAI Grok, DeepSeek, Ollama)."""
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


