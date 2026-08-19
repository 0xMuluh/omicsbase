"""Tests for the provider registry (single source of provider metadata)."""

from __future__ import annotations

from app.services import providers


def test_is_configured_ignores_placeholders(monkeypatch):
    monkeypatch.setattr(providers.settings, "dashscope_api_key", "sk-ws-real-key")
    monkeypatch.setattr(providers.settings, "anthropic_api_key", "sk-ant-...")
    monkeypatch.setattr(providers.settings, "gemini_api_key", "AIza...")
    monkeypatch.setattr(providers.settings, "openrouter_api_key", "your_openrouter_api_key_here")
    monkeypatch.setattr(providers.settings, "orcarouter_api_key", "sk-orca-...")
    assert providers.is_configured("qwen") is True
    assert providers.is_configured("anthropic") is False
    assert providers.is_configured("gemini") is False
    assert providers.is_configured("openrouter") is False
    assert providers.is_configured("orcarouter") is False
    assert providers.is_configured("unknown") is False


def test_orcarouter_resolves_key_and_base_url(monkeypatch):
    monkeypatch.setattr(providers.settings, "orcarouter_api_key", "sk-orca-valid-test-key")
    monkeypatch.setattr(providers.settings, "openai_api_key", "")
    monkeypatch.setattr(providers.settings, "openai_base_url", "")
    assert providers.is_configured("orcarouter") is True
    assert providers.api_key_for("orcarouter") == "sk-orca-valid-test-key"
    assert providers.base_url_for("orcarouter") == "https://api.orcarouter.ai/v1"
    assert providers.default_model_for("orcarouter") == "orcarouter/free"
    assert providers.fast_model_for("orcarouter") == "orcarouter/free"


def test_qwen_resolves_key_and_base_url(monkeypatch):
    monkeypatch.setattr(providers.settings, "dashscope_api_key", "sk-ws-real-key")
    monkeypatch.setattr(providers.settings, "qwen_api_key", "")
    monkeypatch.setattr(providers.settings, "openai_api_key", "")
    monkeypatch.setattr(providers.settings, "qwen_base_url", "")
    assert providers.api_key_for("qwen") == "sk-ws-real-key"
    assert providers.base_url_for("qwen") == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def test_default_model_for_guards_claude_leftover(monkeypatch):
    monkeypatch.setattr(providers.settings, "llm_model", "claude-sonnet-4-20250514")
    assert providers.default_model_for("qwen") == "qwen3.7-plus-2026-05-26"
    monkeypatch.setattr(providers.settings, "llm_model", "qwen3.7-plus-2026-05-26")
    assert providers.default_model_for("qwen") == "qwen3.7-plus-2026-05-26"


def test_fast_model_for_providers():
    assert providers.fast_model_for("qwen") == "qwen3.7-plus-2026-05-26"
    assert providers.fast_model_for("gemini") == "gemini-3.6-flash"
    assert providers.fast_model_for("orcarouter") == "orcarouter/free"
    assert providers.fast_model_for("nope") is None


def test_openai_compat_membership():
    assert providers.is_openai_compat("qwen")
    assert providers.is_openai_compat("gemini")
    assert providers.is_openai_compat("orcarouter")
    assert not providers.is_openai_compat("anthropic")
