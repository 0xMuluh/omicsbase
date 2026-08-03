"""Tests for LLM client reuse and prompt caching."""

from __future__ import annotations

import time
import pytest
from app.services import llm


def test_load_system_prompt_caching(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("System Base Prompt")

    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text("recipes: []")

    monkeypatch.setattr(llm.settings, "prompts_dir", str(prompts_dir))
    monkeypatch.setattr(llm.settings, "registry_path", str(registry_file))

    # Reset module level caches for clean test
    llm._cached_system_prompt = None
    llm._cached_prompt_mtimes = {}

    prompt1 = llm.load_system_prompt()
    assert "System Base Prompt" in prompt1
    assert "Decision-Point Registry" in prompt1

    # Second call should return cached instance without re-reading
    prompt2 = llm.load_system_prompt()
    assert prompt1 is prompt2


def test_client_reuse_singletons(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "test-key-123")

    c1 = llm._get_anthropic_client("test-key-123")
    c2 = llm._get_anthropic_client("test-key-123")
    assert c1 is c2

    c3 = llm._get_openai_client("sk-test-key", "https://api.openai.com/v1")
    c4 = llm._get_openai_client("sk-test-key", "https://api.openai.com/v1")
    assert c3 is c4
