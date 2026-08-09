"""Typed, user-safe failures raised by external language-model providers.

Provider SDKs expose different exception shapes. This module converts the
small set of failures that affect pipeline control flow into stable types so a
quota outage cannot be mistaken for a prompt, parsing, or generation error.
"""

from __future__ import annotations

import json
from typing import Any


class LLMProviderError(RuntimeError):
    """Base class for a classified provider failure."""

    category = "provider_error"
    retryable = False

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.status_code = status_code
        self.public_message = message

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "provider": self.provider,
            "code": self.code,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "message": self.public_message,
        }


class LLMQuotaError(LLMProviderError):
    """Account credits, paid allocation, or provider quota are exhausted."""

    category = "quota_exhausted"
    retryable = False


class LLMAuthenticationError(LLMProviderError):
    """The configured provider credential or account access is invalid."""

    category = "authentication"
    retryable = False


class LLMRateLimitError(LLMProviderError):
    """A temporary request/token rate limit was reached."""

    category = "rate_limited"
    retryable = True


class LLMUnavailableError(LLMProviderError):
    """The provider could not be reached or returned a server failure."""

    category = "unavailable"
    retryable = True


_QUOTA_MARKERS = (
    "allocationquota.freetieronly",
    "insufficient_quota",
    "quota exhausted",
    "quota has been exhausted",
    "free tier is exhausted",
    "free-tier quota",
    "billing quota",
    "credit balance",
    "not enough credits",
    "payment required",
)
_AUTH_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "unauthorized",
    "permission denied",
)
_RATE_LIMIT_MARKERS = (
    "rate_limit",
    "rate limit",
    "too many requests",
    "requests per minute",
    "tokens per minute",
)
_UNAVAILABLE_MARKERS = (
    "connection error",
    "connectionerror",
    "connecterror",
    "connection refused",
    "connection reset",
    "service unavailable",
    "temporarily unavailable",
    "gateway timeout",
    "timed out",
    "timeout",
)


def _safe_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _exception_text(exc: Exception) -> str:
    """Collect error metadata without assuming one provider SDK."""
    parts = [str(exc)]
    for attribute in ("body", "code", "type", "message"):
        value = getattr(exc, attribute, None)
        if value:
            parts.append(_safe_value(value))

    response = getattr(exc, "response", None)
    if response is not None:
        for attribute in ("text", "reason_phrase"):
            value = getattr(response, attribute, None)
            if value:
                parts.append(_safe_value(value))
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                parts.append(_safe_value(json_method()))
            except Exception:
                pass
    return " ".join(part for part in parts if part)


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _provider_code(exc: Exception, text: str) -> str | None:
    code = getattr(exc, "code", None)
    body = getattr(exc, "body", None)
    if not code and isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
        code = code or body.get("code")
    if code:
        return str(code)
    if "allocationquota.freetieronly" in text.lower():
        return "AllocationQuota.FreeTierOnly"
    if "insufficient_quota" in text.lower():
        return "insufficient_quota"
    return None


def classify_provider_exception(exc: Exception, provider: str) -> LLMProviderError | None:
    """Return a stable provider failure, or None for application errors.

    Unknown exceptions intentionally remain untouched: validation and parsing
    bugs should keep their original traceback instead of being mislabeled as a
    provider outage.
    """
    if isinstance(exc, LLMProviderError):
        return exc

    normalized_provider = (provider or "configured").lower()
    text = _exception_text(exc)
    lowered = text.lower()
    status = _status_code(exc)
    code = _provider_code(exc, text)

    if status == 402 or any(marker in lowered for marker in _QUOTA_MARKERS):
        return LLMQuotaError(
            normalized_provider,
            f"The {normalized_provider} language-model quota is exhausted. Add billing or credits, "
            "or switch providers, then explicitly retry the build; completed units are preserved.",
            code=code,
            status_code=status,
        )

    if status == 429 or any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return LLMRateLimitError(
            normalized_provider,
            f"The {normalized_provider} language-model service is temporarily rate-limited. "
            "Wait briefly, then resume the build.",
            code=code,
            status_code=status,
        )

    if status in {401, 403} or any(marker in lowered for marker in _AUTH_MARKERS):
        return LLMAuthenticationError(
            normalized_provider,
            f"The {normalized_provider} language-model credentials or account access were rejected. "
            "Check the configured key and account permissions before retrying.",
            code=code,
            status_code=status,
        )

    if (status is not None and status >= 500) or any(marker in lowered for marker in _UNAVAILABLE_MARKERS):
        return LLMUnavailableError(
            normalized_provider,
            f"The {normalized_provider} language-model service is unavailable. "
            "The build stopped without discarding completed units; resume it when service returns.",
            code=code,
            status_code=status,
        )

    return None


def raise_classified_provider_exception(exc: Exception, provider: str) -> None:
    """Raise a typed failure when recognized, otherwise re-raise exc."""
    classified = classify_provider_exception(exc, provider)
    if classified is None or classified is exc:
        raise exc
    raise classified from exc
