"""Provider failure classification and planner control-flow tests."""

from __future__ import annotations

import pytest

from app.services import llm, providers
from app.services.provider_errors import (
    LLMAuthenticationError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMUnavailableError,
    classify_provider_exception,
)


class FakeProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_dashscope_free_tier_is_non_retryable_quota_failure():
    failure = classify_provider_exception(
        FakeProviderError(
            "Error code: 403 - AllocationQuota.FreeTierOnly",
            status_code=403,
            body={"code": "AllocationQuota.FreeTierOnly"},
        ),
        "qwen",
    )

    assert isinstance(failure, LLMQuotaError)
    assert failure.retryable is False
    assert failure.code == "AllocationQuota.FreeTierOnly"
    assert "billing or credits" in str(failure)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FakeProviderError("Too many requests", status_code=429), LLMRateLimitError),
        (FakeProviderError("invalid API key", status_code=401), LLMAuthenticationError),
        (FakeProviderError("upstream failed", status_code=503), LLMUnavailableError),
        (RuntimeError("Connection error."), LLMUnavailableError),
    ],
)
def test_provider_failure_categories(error, expected):
    assert isinstance(classify_provider_exception(error, "qwen"), expected)


@pytest.mark.asyncio
async def test_call_llm_raises_typed_quota_failure(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise FakeProviderError(
            "AllocationQuota.FreeTierOnly",
            status_code=403,
            body={"code": "AllocationQuota.FreeTierOnly"},
        )

    monkeypatch.setattr(llm, "_call_openai", fail)
    monkeypatch.setattr(llm.settings, "llm_provider", "qwen")

    with pytest.raises(LLMQuotaError):
        await llm.call_llm("system", "user")
