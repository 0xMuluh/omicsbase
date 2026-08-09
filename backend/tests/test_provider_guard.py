"""Durable provider guard behavior."""

from types import SimpleNamespace

from app.services.provider_errors import LLMQuotaError, LLMUnavailableError
from app.services.provider_guard import (
    active_provider_block,
    clear_provider_block,
    record_provider_block,
)


def test_non_retryable_failure_blocks_only_the_failed_provider():
    project = SimpleNamespace(agent_memory={"summary": "keep"})
    failure = LLMQuotaError(
        "qwen",
        "quota exhausted",
        code="AllocationQuota.FreeTierOnly",
        status_code=403,
    )

    assert record_provider_block(project, failure) is True
    assert active_provider_block(project, "qwen")["category"] == "quota_exhausted"
    assert active_provider_block(project, "openai") is None
    assert project.agent_memory["summary"] == "keep"


def test_retryable_outage_does_not_persist_a_block():
    project = SimpleNamespace(agent_memory={})
    failure = LLMUnavailableError("qwen", "temporarily unavailable", status_code=503)

    assert record_provider_block(project, failure) is False
    assert active_provider_block(project, "qwen") is None


def test_success_clears_only_the_matching_provider_block():
    project = SimpleNamespace(
        agent_memory={
            "provider_blocks": {
                "qwen": {"retryable": False},
                "openai": {"retryable": False},
            }
        }
    )

    assert clear_provider_block(project, "qwen") is True
    assert active_provider_block(project, "qwen") is None
    assert active_provider_block(project, "openai") is not None
