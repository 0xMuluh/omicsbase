"""Compatibility façade for configurable LLM providers.

Provider implementations live under ``llm_providers``.  This module retains
the historical public and private import surface while keeping dispatch,
client caching, and prompt loading centralized.
"""

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



# Provider compatibility wrappers.  Provider modules resolve client helpers
# through this façade at call time, so existing monkeypatch/import seams stay
# valid while implementation ownership is provider-specific.
from app.services.llm_providers import anthropic as _anthropic_provider
from app.services.llm_providers import gemini as _gemini_provider
from app.services.llm_providers import openai as _openai_provider


def _gemini_config(*args: Any, **kwargs: Any) -> Any:
    return _gemini_provider._gemini_config(*args, **kwargs)


def _clean_schema_for_gemini(schema: Any) -> Any:
    return _gemini_provider._clean_schema_for_gemini(schema)


def _gemini_tools(tools: list[dict[str, Any]]) -> list[Any] | None:
    return _gemini_provider._gemini_tools(tools)


def _openai_to_gemini_contents(messages: list[dict[str, Any]]) -> tuple[list[Any], dict[str, str]]:
    return _gemini_provider._openai_to_gemini_contents(messages)


async def _call_gemini(*args: Any, **kwargs: Any) -> str:
    return await _gemini_provider._call_gemini(*args, **kwargs)


async def _stream_gemini(*args: Any, **kwargs: Any):
    async for chunk in _gemini_provider._stream_gemini(*args, **kwargs):
        yield chunk


async def _stream_gemini_with_tools(*args: Any, **kwargs: Any):
    async for event in _gemini_provider._stream_gemini_with_tools(*args, **kwargs):
        yield event


async def _call_anthropic(*args: Any, **kwargs: Any) -> str:
    return await _anthropic_provider._call_anthropic(*args, **kwargs)


async def _stream_anthropic(*args: Any, **kwargs: Any):
    async for chunk in _anthropic_provider._stream_anthropic(*args, **kwargs):
        yield chunk


def _convert_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _anthropic_provider._convert_anthropic_tools(tools)


async def _stream_anthropic_with_tools(*args: Any, **kwargs: Any):
    async for event in _anthropic_provider._stream_anthropic_with_tools(*args, **kwargs):
        yield event


async def _call_openai(*args: Any, **kwargs: Any) -> str:
    return await _openai_provider._call_openai(*args, **kwargs)


async def _stream_openai(*args: Any, **kwargs: Any):
    async for chunk in _openai_provider._stream_openai(*args, **kwargs):
        yield chunk


def _resolve_openai_provider(*args: Any, **kwargs: Any):
    return _openai_provider._resolve_openai_provider(*args, **kwargs)


def _normalise_tool_arguments(arguments: Any) -> dict[str, Any]:
    return _openai_provider._normalise_tool_arguments(arguments)


def _normalise_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _openai_provider._normalise_openai_messages(messages)


def _usage_payload(usage: Any) -> dict[str, int] | None:
    return _openai_provider._usage_payload(usage)


async def _stream_openai_with_tools(*args: Any, **kwargs: Any):
    async for event in _openai_provider._stream_openai_with_tools(*args, **kwargs):
        yield event


def _set_token_limit(*args: Any, **kwargs: Any) -> None:
    return _openai_provider._set_token_limit(*args, **kwargs)


def _supports_reasoning_effort(*args: Any, **kwargs: Any) -> bool:
    return _openai_provider._supports_reasoning_effort(*args, **kwargs)


def _should_retry_with_alternate_token_param(*args: Any, **kwargs: Any) -> bool:
    return _openai_provider._should_retry_with_alternate_token_param(*args, **kwargs)
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
