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


def test_anthropic_tools_block_is_cached():
    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "list_files", "description": "List files", "parameters": {"type": "object"}}},
    ]
    converted = llm._convert_anthropic_tools(tools)
    assert converted[0].get("cache_control") is None
    assert converted[1]["cache_control"] == {"type": "ephemeral"}
    assert converted[0]["name"] == "read_file"


def test_anthropic_live_context_block_is_cached(monkeypatch):
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def get_final_message(self):
                return type("F", (), {"usage": None})()

        return FakeStream()

    class FakeClient:
        messages = type("M", (), {"stream": staticmethod(fake_stream)})()

    monkeypatch.setattr(llm, "_get_async_anthropic_client", lambda key: FakeClient())
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "test-key-123")

    async def collect():
        events = []
        async for event in llm._stream_anthropic_with_tools(
            "system", [{"role": "user", "content": "hi"}], [], 100, live_context="snapshot",
        ):
            events.append(event)
        return events

    import asyncio

    asyncio.run(collect())
    system_blocks = captured["system"]
    assert len(system_blocks) == 2
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_reasoning_effort_gate():
    assert llm._supports_reasoning_effort("openai", "gpt-5.6-luna") is True
    assert llm._supports_reasoning_effort("openai", "gpt-4o") is False
    assert llm._supports_reasoning_effort("groq", "llama-3.3-70b-versatile") is False


def test_tools_path_forces_reasoning_effort_none_on_gpt5(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        class FakeChoice:
            message = type("M", (), {"model_dump": lambda self: {"content": ""}})()
            finish_reason = "stop"
        class FakeResp:
            choices = [FakeChoice()]

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return FakeResp()

    class FakeCompletions:
        create = staticmethod(fake_create)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(llm, "_get_async_openai_client", lambda key, base: FakeClient())
    monkeypatch.setattr(llm.settings, "openai_api_key", "sk-test")

    async def collect():
        events = []
        async for event in llm._stream_openai_with_tools(
            "system",
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "inspect_note", "description": "d", "parameters": {"type": "object"}}}],
            100,
            provider="openai",
            model_override="gpt-5.6-luna",
        ):
            events.append(event)
        return events

    import asyncio

    asyncio.run(collect())
    assert captured["reasoning_effort"] == "none"
    assert captured["max_completion_tokens"] == 100


def test_tools_path_does_not_force_effort_on_other_models(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        class FakeChoice:
            message = type("M", (), {"model_dump": lambda self: {"content": ""}})()
            finish_reason = "stop"
        class FakeResp:
            choices = [FakeChoice()]

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return FakeResp()

    class FakeCompletions:
        create = staticmethod(fake_create)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(llm, "_get_async_openai_client", lambda key, base: FakeClient())
    monkeypatch.setattr(llm.settings, "groq_api_key", "sk-test")

    async def collect():
        events = []
        async for event in llm._stream_openai_with_tools(
            "system",
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "inspect_note", "description": "d", "parameters": {"type": "object"}}}],
            100,
            provider="groq",
            model_override="llama-3.3-70b-versatile",
        ):
            events.append(event)
        return events

    import asyncio

    asyncio.run(collect())
    assert "reasoning_effort" not in captured
    assert captured["max_tokens"] == 100
