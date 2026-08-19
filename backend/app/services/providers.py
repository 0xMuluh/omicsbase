"""Single provider registry consumed by the LLM layer, planner, and fast path.

Every consumer reads provider metadata from here instead of hand-rolling
key/model/base-url mappings. Settings continue to come from ``.env`` via
``app.config.settings``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata for one LLM provider."""

    key_fields: tuple[str, ...]          # settings fields tried in order for the API key
    base_url_field: str | None           # optional settings field for a custom base URL
    default_base_url: str | None         # fallback base URL when not configured
    default_model: str                   # model used when settings.llm_model is not valid
    fast_model: str                      # model for the fast path when no target override


# Placeholder literals used in .env.example that must not be treated as keys.
_PLACEHOLDER_PREFIXES = (
    "your_",
    "sk-...",
    "sk-ant-...",
    "sk-or-...",
    "aiza...",
    "gsk_...",
    "sk-proj-...",
    "sk-orca-...",
)

_PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        key_fields=("anthropic_api_key",),
        base_url_field=None,
        default_base_url=None,
        default_model="claude-sonnet-4-20250514",
        fast_model="claude-haiku-4-5",
    ),
    "openai": ProviderSpec(
        key_fields=("openai_api_key",),
        base_url_field="openai_base_url",
        default_base_url=None,
        default_model="gpt-4o",
        fast_model="gpt-4o-mini",
    ),
    "qwen": ProviderSpec(
        key_fields=("dashscope_api_key", "qwen_api_key", "openai_api_key"),
        base_url_field="qwen_base_url",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3.7-plus-2026-05-26",
        fast_model="qwen3.7-plus-2026-05-26",
    ),
    "gemini": ProviderSpec(
        key_fields=("gemini_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-3.6-flash",
        fast_model="gemini-3.6-flash",
    ),
    "openrouter": ProviderSpec(
        key_fields=("openrouter_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-3.5-sonnet",
        fast_model="anthropic/claude-3.5-haiku",
    ),
    "orcarouter": ProviderSpec(
        key_fields=("orcarouter_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://api.orcarouter.ai/v1",
        default_model="orcarouter/free",
        fast_model="orcarouter/free",
    ),
    "deepseek": ProviderSpec(
        key_fields=("openai_api_key",),
        base_url_field="openai_base_url",
        default_base_url=None,
        default_model="deepseek-chat",
        fast_model="deepseek-chat",
    ),
    "groq": ProviderSpec(
        key_fields=("groq_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        fast_model="llama-3.3-70b-versatile",
    ),
    "grok": ProviderSpec(
        key_fields=("grok_api_key", "xai_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://api.x.ai/v1",
        default_model="grok-2-latest",
        fast_model="grok-2-latest",
    ),
    "xai": ProviderSpec(
        key_fields=("grok_api_key", "xai_api_key", "openai_api_key"),
        base_url_field="openai_base_url",
        default_base_url="https://api.x.ai/v1",
        default_model="grok-2-latest",
        fast_model="grok-2-latest",
    ),
    "ollama": ProviderSpec(
        key_fields=(),
        base_url_field="openai_base_url",
        default_base_url=None,
        default_model="",
        fast_model="",
    ),
}

_OPENAI_COMPAT_PROVIDERS = {
    "openai", "qwen", "gemini", "openrouter", "orcarouter", "deepseek", "groq", "grok", "xai", "ollama",
}


def spec_for(provider: str) -> ProviderSpec | None:
    """Return the provider spec, or None for unknown providers."""
    return _PROVIDERS.get(provider)


def is_openai_compat(provider: str) -> bool:
    """Whether the provider speaks the OpenAI chat-completions protocol."""
    return provider in _OPENAI_COMPAT_PROVIDERS


def api_key_for(provider: str) -> str:
    """Resolve the configured API key for a provider from settings (``.env``)."""
    spec = _PROVIDERS.get(provider)
    if spec is None:
        return ""
    for field in spec.key_fields:
        value = getattr(settings, field, "") or ""
        if value.strip():
            return value.strip()
    return ""


def base_url_for(provider: str) -> str | None:
    """Resolve the API base URL for a provider."""
    spec = _PROVIDERS.get(provider)
    if spec is None:
        return None
    if spec.base_url_field:
        configured = getattr(settings, spec.base_url_field, "") or ""
        if configured.strip():
            return configured.strip()
    return spec.default_base_url


def default_model_for(provider: str, current: str | None = None) -> str:
    """Return a valid model for the provider.

    ``current`` is the global ``LLM_MODEL``; provider-unrelated leftovers
    (Claude defaults and similar) fall back to the provider's default model.
    """
    spec = _PROVIDERS.get(provider)
    if spec is None:
        return current or ""
    model = (current or "").strip()
    if model and "claude" not in model.lower():
        return model
    return spec.default_model


def fast_model_for(provider: str) -> str | None:
    """Return the fast-path model for a provider."""
    spec = _PROVIDERS.get(provider)
    return spec.fast_model if spec else None


def is_configured(provider: str) -> bool:
    """Whether a real (non-placeholder) API key is configured for a provider."""
    key = api_key_for(provider)
    if not key:
        return False
    lowered = key.lower()
    if any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return False
    return True
