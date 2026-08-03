import time
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import llm
from app.config import settings


def test_load_system_prompt_caching(tmp_path, monkeypatch):
    llm._cached_system_prompt = None
    llm._cached_prompt_mtimes = {}
    # Setup temporary prompts dir and registry file
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    system_md = prompts_dir / "system.md"
    system_md.write_text("Initial system prompt v1")

    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text("decision: initial")

    # Patch settings to point to temp files
    monkeypatch.setattr(settings, "prompts_dir", str(prompts_dir))
    monkeypatch.setattr(settings, "registry_path", str(registry_file))

    # First load should read the files
    first = llm.load_system_prompt()
    assert "Initial system prompt v1" in first
    assert "decision: initial" in first

    # Second load without changes should return same cached string
    second = llm.load_system_prompt()
    assert first == second

    # Modify the system prompt to simulate an update
    system_md.write_text("Updated system prompt v2")
    # Ensure mtime definitely changes on filesystems with coarse timestamps
    os.utime(system_md, None)

    third = llm.load_system_prompt()
    assert "Updated system prompt v2" in third
    assert third != first


def test_openai_client_reuse():
    # Use a dummy api key and base_url
    api_key = "test-key"
    base_url = "https://example.com/"

    c1 = llm._get_openai_client(api_key, base_url)
    c2 = llm._get_openai_client(api_key, base_url)
    assert c1 is c2

    # Different key or base_url should yield a different client object
    c3 = llm._get_openai_client("other-key", base_url)
    assert c3 is not c1


@pytest.mark.asyncio
async def test_async_openai_client_reuse():
    api_key = "test-key"
    base_url = None

    c1 = llm._get_async_openai_client(api_key, base_url)
    c2 = llm._get_async_openai_client(api_key, base_url)
    assert c1 is c2

    c3 = llm._get_async_openai_client("other-key", base_url)
    assert c3 is not c1


@pytest.mark.asyncio
async def test_openai_tool_history_preserves_protocol_fields(monkeypatch):
    recorded: dict[str, object] = {}

    async def empty_stream():
        if False:
            yield None

    async def create(**kwargs):
        recorded.update(kwargs)
        return empty_stream()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )
    monkeypatch.setattr(llm, "_get_async_openai_client", lambda *_args: fake_client)
    monkeypatch.setattr(llm.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "llm_model", "test-model")

    messages = [
        {"role": "user", "content": "Inspect the workspace."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "inspect_workspace",
                        "arguments": '{"path":"data"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"status":"ok"}',
        },
    ]

    events = [
        event
        async for event in llm._stream_openai_with_tools(
            system_prompt="Stable instructions",
            messages=messages,
            tools=[],
            max_tokens=100,
            provider="openai",
        )
    ]

    assert events == [{"type": "done"}]
    assert recorded["messages"] == [
        {"role": "system", "content": "Stable instructions"},
        *messages,
    ]


@pytest.mark.asyncio
async def test_openai_tool_stream_reports_provider_usage(monkeypatch):
    async def usage_stream():
        yield SimpleNamespace(choices=[], usage={"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19})

    async def create(**kwargs):
        return usage_stream()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )
    monkeypatch.setattr(llm, "_get_async_openai_client", lambda *_args: fake_client)
    monkeypatch.setattr(llm.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "llm_model", "test-model")

    events = [
        event
        async for event in llm._stream_openai_with_tools(
            system_prompt="Stable instructions",
            messages=[{"role": "user", "content": "Inspect the workspace."}],
            tools=[],
            max_tokens=100,
            provider="openai",
        )
    ]

    assert events == [
        {"type": "usage", "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}},
        {"type": "done"},
    ]
