"""Durable project-level guard for non-retryable LLM provider failures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.provider_errors import (
    LLMAuthenticationError,
    LLMProviderError,
    LLMQuotaError,
)


_MEMORY_KEY = "provider_blocks"


def _provider_name(provider: str | None) -> str:
    return (provider or "configured").strip().lower()


def active_provider_block(project: Any, provider: str | None) -> dict[str, Any] | None:
    """Return the non-retryable block for a provider, if one is persisted."""
    memory = dict(getattr(project, "agent_memory", None) or {})
    blocks = memory.get(_MEMORY_KEY)
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(_provider_name(provider))
    if not isinstance(block, dict) or block.get("retryable", True):
        return None
    return dict(block)


def provider_error_from_block(block: dict[str, Any]) -> LLMProviderError:
    """Rehydrate a persisted non-retryable provider failure for task control flow."""
    category = str(block.get("category") or "provider_error")
    error_type = {
        "quota_exhausted": LLMQuotaError,
        "authentication": LLMAuthenticationError,
    }.get(category, LLMProviderError)
    return error_type(
        str(block.get("provider") or "configured"),
        str(block.get("message") or "The configured language-model provider is blocked."),
        code=str(block.get("code")) if block.get("code") is not None else None,
        status_code=int(block["status_code"]) if block.get("status_code") is not None else None,
    )


def record_provider_block(project: Any, failure: LLMProviderError) -> bool:
    """Persist a non-retryable provider failure in project agent memory."""
    if failure.retryable:
        return False
    memory = dict(getattr(project, "agent_memory", None) or {})
    blocks = dict(memory.get(_MEMORY_KEY) or {})
    payload = failure.as_dict()
    payload["observed_at"] = datetime.now(timezone.utc).isoformat()
    blocks[_provider_name(failure.provider)] = payload
    memory[_MEMORY_KEY] = blocks
    project.agent_memory = memory
    return True


def clear_provider_block(project: Any, provider: str | None) -> bool:
    """Clear one provider block after a successful real provider call."""
    memory = dict(getattr(project, "agent_memory", None) or {})
    blocks = dict(memory.get(_MEMORY_KEY) or {})
    if blocks.pop(_provider_name(provider), None) is None:
        return False
    if blocks:
        memory[_MEMORY_KEY] = blocks
    else:
        memory.pop(_MEMORY_KEY, None)
    project.agent_memory = memory
    return True
