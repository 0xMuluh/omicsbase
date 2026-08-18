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


def test_load_system_prompt_routes_only_requested_scientific_references(
    tmp_path,
    monkeypatch,
):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("Generic omics system")
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text("decisions: []")
    skills_dir = tmp_path / "skills"
    writing = skills_dir / "quarto-research-report" / "references"
    writing.mkdir(parents=True)
    (writing / "writing-style.md").write_text("Generic report voice")
    micro = skills_dir / "microbiome-analysis" / "references"
    micro.mkdir(parents=True)
    (micro / "data-rules.md").write_text("MICROBIOME_ONLY_KNOWLEDGE")
    meta = skills_dir / "metabolomics-analysis" / "references"
    meta.mkdir(parents=True)
    (meta / "model-rules.md").write_text("METABOLOMICS_ONLY_KNOWLEDGE")

    monkeypatch.setattr(settings, "prompts_dir", str(prompts_dir))
    monkeypatch.setattr(settings, "registry_path", str(registry_file))
    monkeypatch.setattr(settings, "skills_dir", str(skills_dir))
    llm._cached_system_prompt = None
    llm._cached_prompt_mtimes = {}

    microbiome_prompt = llm.load_system_prompt(
        ["microbiome-analysis/references/data-rules.md"],
        include_registry=False,
    )

    assert "Generic report voice" in microbiome_prompt
    assert "MICROBIOME_ONLY_KNOWLEDGE" in microbiome_prompt
    assert "METABOLOMICS_ONLY_KNOWLEDGE" not in microbiome_prompt
    assert "Decision-Point Registry" not in microbiome_prompt

    metabolomics_prompt = llm.load_system_prompt(
        ["metabolomics-analysis/references/model-rules.md"],
        include_registry=False,
    )

    assert "METABOLOMICS_ONLY_KNOWLEDGE" in metabolomics_prompt
    assert "MICROBIOME_ONLY_KNOWLEDGE" not in metabolomics_prompt


def test_load_system_prompt_rejects_escaping_reference(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("Generic omics system")
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text("decisions: []")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    monkeypatch.setattr(settings, "prompts_dir", str(prompts_dir))
    monkeypatch.setattr(settings, "registry_path", str(registry_file))
    monkeypatch.setattr(settings, "skills_dir", str(skills_dir))

    with pytest.raises(ValueError, match="Unsafe scientific prompt reference"):
        llm.load_system_prompt(["../secret.md"])


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
    create_calls = []

    async def usage_stream():
        yield SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=None),
                finish_reason="stop",
            )],
            usage=None,
        )
        yield SimpleNamespace(choices=[], usage={"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19})

    async def create(**kwargs):
        create_calls.append(kwargs)
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

    assert len(create_calls) == 1
    assert events == [
        {"type": "usage", "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}},
        {"type": "done"},
    ]


@pytest.mark.asyncio
async def test_qwen_stream_requests_usage_and_retries_stall(monkeypatch):
    """Qwen streams must ask for usage and survive a pre-output stall once."""
    import json as _json

    captured_kwargs: list[dict] = []
    attempts = {"count": 0}

    class _Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Choice:
        def __init__(self, delta):
            self.delta = delta

    class _Chunk:
        def __init__(self, delta=None, usage=None):
            self.choices = [_Choice(delta)] if delta else []
            self.usage = usage

    class _Completions:
        async def create(self, **kwargs):
            captured_kwargs.append(kwargs)
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("connection timed out before any output")

            async def stream():
                yield _Chunk(_Delta(content="recovered"))
                yield _Chunk(usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})

            return stream()

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(llm, "_get_async_openai_client", lambda api_key, base_url: _Client())
    events = [event async for event in llm._stream_openai_with_tools(
        "system", [{"role": "user", "content": "hi"}], [], 100, provider="qwen",
    )]
    assert attempts["count"] == 2, "pre-output stall must retry exactly once"
    assert captured_kwargs[0]["stream_options"] == {"include_usage": True}
    assert any(e["type"] == "text_delta" and e["content"] == "recovered" for e in events)
    assert any(e["type"] == "usage" and e["usage"]["total_tokens"] == 12 for e in events)


@pytest.mark.asyncio
async def test_midstream_failure_is_not_retried(monkeypatch):
    """Output already yielded means a retry would duplicate content: raise instead."""
    attempts = {"count": 0}

    class _Delta:
        def __init__(self, content=None):
            self.content = content
            self.tool_calls = None

    class _Choice:
        def __init__(self, delta):
            self.delta = delta

    class _Chunk:
        def __init__(self, delta):
            self.choices = [_Choice(delta)]

    class _Completions:
        async def create(self, **kwargs):
            attempts["count"] += 1

            async def stream():
                yield _Chunk(_Delta(content="partial"))
                raise RuntimeError("died mid-stream")

            return stream()

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(llm, "_get_async_openai_client", lambda api_key, base_url: _Client())
    collected: list[dict] = []
    with pytest.raises(RuntimeError):
        async for event in llm._stream_openai_with_tools(
            "system", [{"role": "user", "content": "hi"}], [], 100, provider="qwen",
        ):
            collected.append(event)
    assert attempts["count"] == 1
    assert collected and collected[0]["content"] == "partial"


def test_openai_clients_carry_request_timeout():
    client = llm._get_openai_client("timeout-key-1", "https://example.com/")
    assert client.timeout == llm._request_timeout_seconds()
