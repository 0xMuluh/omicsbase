"""Tests for the native Gemini SDK path in the LLM layer."""

from __future__ import annotations

import pytest
from app.services import llm


def test_resolve_gemini_model_accepts_frontier_names(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_model", "gemini-3.1-pro-preview")
    assert llm._resolve_gemini_model(None) == "gemini-3.1-pro-preview"
    assert llm._resolve_gemini_model("gemini-3.1-pro-preview") == "gemini-3.1-pro-preview"
    assert llm._resolve_gemini_model("gemini-3.6-flash") == "gemini-3.6-flash"


def test_resolve_gemini_model_guards_non_gemini_names(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_model", "claude-sonnet-4-20250514")
    expected = llm.default_model_for("gemini") or llm._GEMINI_FALLBACK_MODEL
    assert llm._resolve_gemini_model(None) == expected
    assert llm._resolve_gemini_model("deepseek-v4-pro") == expected
    assert llm._resolve_gemini_model("gpt-5.6-luna") == expected


def test_openai_to_gemini_contents_roundtrip():
    messages = [
        {"role": "user", "content": "Run an analysis"},
        {
            "role": "assistant",
            "content": "Let me run R",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_r", "arguments": '{"code": "1+1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "ok"}'},
    ]
    contents, tool_map = llm._openai_to_gemini_contents(messages)
    assert tool_map == {"call_1": "run_r"}

    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Run an analysis"

    model_content = contents[1]
    assert model_content.role == "model"
    assert model_content.parts[0].text == "Let me run R"
    fc = model_content.parts[1].function_call
    assert fc.id == "call_1"
    assert fc.name == "run_r"
    assert fc.args == {"code": "1+1"}

    tool_content = contents[2]
    assert tool_content.role == "user"
    fr = tool_content.parts[0].function_response
    assert fr.name == "run_r"
    assert fr.response == {"status": "ok"}


def test_openai_to_gemini_contents_string_arguments(monkeypatch):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c2", "function": {"name": "read_file", "arguments": '{"path": "x"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "c2", "content": '{"found": true}'},
    ]
    contents, _ = llm._openai_to_gemini_contents(messages)
    assert contents[0].parts[0].function_call.args == {"path": "x"}
    assert contents[1].parts[0].function_response.name == "read_file"


def test_openai_to_gemini_contents_bad_json_falls_back():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "c3", "function": {"name": "run_r", "arguments": "not json"}}]},
        {"role": "tool", "tool_call_id": "c3", "content": "plain text"},
    ]
    contents, _ = llm._openai_to_gemini_contents(messages)
    assert contents[0].parts[0].function_call.args == {}
    assert contents[1].parts[0].function_response.response == {"output": "plain text"}


def test_gemini_tools_conversion():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_r",
                "description": "Run R code",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
            },
        }
    ]
    converted = llm._gemini_tools(tools)
    assert converted is not None
    declaration = converted[0].function_declarations[0]
    assert declaration.name == "run_r"
    assert declaration.description == "Run R code"
    from google.genai import types

    assert declaration.parameters.type == types.Type.OBJECT


def test_gemini_config_thinking_budget(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_thinking_budget", 1024)
    config = llm._gemini_config("sys", None, 8000, response_mime_type="application/json")
    assert config.max_output_tokens == 8000
    assert config.response_mime_type == "application/json"
    assert config.system_instruction == "sys"
    assert config.thinking_config.thinking_budget == 1024

    config_default = llm._gemini_config("sys", None, 8000, thinking_budget=0)
    assert config_default.thinking_config is None


@pytest.mark.asyncio
async def test_call_llm_routes_gemini_to_native(monkeypatch):
    captured = {}

    async def fake_call_gemini(system_prompt, user_prompt, response_format, max_tokens, model_override=None):
        captured.update(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
            max_tokens=max_tokens,
            model_override=model_override,
        )
        return "native response"

    monkeypatch.setattr(llm.settings, "llm_provider", "gemini")
    monkeypatch.setattr(llm.settings, "gemini_native", True)
    monkeypatch.setattr(llm, "_call_gemini", fake_call_gemini)

    result = await llm.call_llm("sys", "user", response_format="json", max_tokens=1234, model_override="gemini-3.1-pro-preview")
    assert result == "native response"
    assert captured == {
        "system_prompt": "sys",
        "user_prompt": "user",
        "response_format": "json",
        "max_tokens": 1234,
        "model_override": "gemini-3.1-pro-preview",
    }


@pytest.mark.asyncio
async def test_call_llm_gemini_compat_fallback_when_native_disabled(monkeypatch):
    captured = {}

    async def fake_call_openai(system_prompt, user_prompt, response_format, max_tokens, provider="openai", model_override=None, reasoning_effort=None):
        captured.update(provider=provider, model_override=model_override)
        return "compat response"

    monkeypatch.setattr(llm.settings, "llm_provider", "gemini")
    monkeypatch.setattr(llm.settings, "gemini_native", False)
    monkeypatch.setattr(llm, "_call_openai", fake_call_openai)

    result = await llm.call_llm("sys", "user")
    assert result == "compat response"
    assert captured == {"provider": "gemini", "model_override": None}


@pytest.mark.asyncio
async def test_stream_gemini_with_tools_event_shape(monkeypatch):
    class FakeFunctionCall:
        def __init__(self, call_id, name, args):
            self.id = call_id
            self.name = name
            self.args = args

    class FakePart:
        def __init__(self, text=None, function_call=None, thought_signature=None):
            self.text = text
            self.function_call = function_call
            self.thought_signature = thought_signature

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, parts):
            self.content = FakeContent(parts)

    class FakeChunk:
        def __init__(self, parts=None, text=None, usage=None):
            self.candidates = [FakeCandidate(parts or [])]
            self.text = text
            self.usage_metadata = usage

    chunk1 = FakeChunk(
        parts=[FakePart(
            function_call=FakeFunctionCall("call_1", "run_r", {"code": "1+"}),
            thought_signature=b"sig-bytes",
        )],
        usage=type("U", (), {"prompt_token_count": 10, "candidates_token_count": 5})(),
    )
    chunk2 = FakeChunk(parts=[FakePart(function_call=FakeFunctionCall("call_1", "", {"code": "1+1"}))])
    chunk3 = FakeChunk(text="done")

    class FakeStream:
        def __init__(self, chunks):
            self._chunks = chunks

        def __aiter__(self):
            self._iter = iter(self._chunks)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    class FakeModels:
        def generate_content_stream(self, **kwargs):
            return FakeStream([chunk1, chunk2, chunk3])

    class FakeAio:
        models = FakeModels()

    class FakeClient:
        aio = FakeAio()

    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm, "_get_gemini_client", lambda api_key: FakeClient())

    events = []
    async for event in llm._stream_gemini_with_tools(
        system_prompt="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "run_r", "description": "d", "parameters": {"type": "object"}}}],
        max_tokens=4000,
    ):
        events.append(event)

    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["id"] == "call_1"
    assert tool_events[0]["name"] == "run_r"
    assert tool_events[0]["arguments"] == {"code": "1+1"}
    import base64
    assert tool_events[0]["thought_signature"] == base64.b64encode(b"sig-bytes").decode("ascii")
    assert events[-1] == {"type": "done"}
    usage = [e for e in events if e["type"] == "usage"]
    assert usage == [{"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5}}]
