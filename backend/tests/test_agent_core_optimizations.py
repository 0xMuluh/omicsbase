"""Tests for the agent-core optimizations: LLM-judge routing, delta context,
and parallel read-only tool execution."""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services import agent_core
from app.services.agent_core import ToolCallResult, UNCHANGED_CONTEXT
from app.services.intent_fastpath import classify_intent, is_simple_question


class StubExecutor:
    max_steps = 4
    max_tokens = 100
    max_tool_chars = 2000
    system_prompt = ""
    tools = []
    use_retry_guard = False
    cancelled_message = "cancelled"
    default_final_message = "default"
    max_steps_message = "max"
    llm_provider_override = None
    llm_model_override = None

    def __init__(self):
        self.build_calls = 0
        self.tool_calls = []
        self.active = 0
        self.max_active = 0

    def initial_events(self, message):
        return [], False

    def build_messages(self, message):
        return [{"role": "user", "content": message}]

    def build_live_context(self):
        self.build_calls += 1
        return "SNAPSHOT"

    def fallback_events(self, exc):
        return [{"type": "final", "message": "fallback"}]

    def final_event(self, message):
        return {"type": "final", "message": message}

    def max_steps_events(self):
        return [{"type": "final", "message": "max"}]

    def tool_completed_event(self, *args, **kwargs):
        return {"type": "tool_completed"}

    def summary_for(self, tool_name, observation):
        return "summary"

    def use_fast_path(self, message):
        return False

    async def judge_intent(self, message):
        return "conceptual"

    async def fast_path_events(self, message, *, intent="conceptual"):
        yield {"type": "token", "token": "fast "}
        yield {"type": "final", "message": "fast answer", "fast": True}

    def parallel_eligible(self, tool_name):
        return tool_name == "read_file"

    async def execute_tool(self, tool_name, arguments, *, step, tool_call_id, persisted_arguments, step_text):
        self.tool_calls.append((tool_name, tool_call_id))
        if tool_name == "read_file":
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
        return ToolCallResult(observation={"status": "ok", "tool": tool_name})

    async def legacy_llm_step(self, messages, *, step):
        return None


def _events(executor, message, llm_script):
    """Run one loop turn with a scripted stream_llm_with_tools.

    llm_script: list of per-step payloads; each is a list of event dicts.
    A step without tool_call events ends the turn with collected text.
    """

    async def fake_stream_llm_with_tools(**kwargs):
        payload = llm_script.pop(0)
        for event in payload:
            yield event
        yield {"type": "done"}

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, message)]

    original = agent_core.stream_llm_with_tools
    agent_core.stream_llm_with_tools = fake_stream_llm_with_tools
    try:
        return asyncio.run(collect())
    finally:
        agent_core.stream_llm_with_tools = original


def test_judge_routes_needs_tools_to_full_loop(monkeypatch):
    executor = StubExecutor()

    async def judge(self, message):
        return "needs_tools"

    executor.judge_intent = judge.__get__(executor)
    executor.use_fast_path = lambda message: True

    script = [
        [{"type": "tool_call", "id": "call_1", "name": "inspect_project", "arguments": {}}],
        [{"type": "text_delta", "content": "done"}],
    ]
    events = _events(executor, "What is a p-value?", script)
    assert executor.tool_calls == [("inspect_project", "call_1")]
    assert events[-1]["message"] == "done"
    assert not any(e.get("fast") for e in events)


def test_judge_conceptual_takes_fast_path(monkeypatch):
    executor = StubExecutor()
    executor.use_fast_path = lambda message: True
    script = [[{"type": "text_delta", "content": "should not run"}]]
    events = _events(executor, "What is a p-value?", script)
    assert executor.tool_calls == []
    assert events[-1]["message"] == "fast answer"
    assert events[-1]["fast"] is True


def test_delta_context_sends_snapshot_once():
    executor = StubExecutor()
    live_contexts = []

    async def fake_stream_llm_with_tools(**kwargs):
        live_contexts.append(kwargs.get("live_context"))
        step = len(live_contexts)
        if step == 1:
            yield {"type": "tool_call", "id": "call_1", "name": "list_files", "arguments": {}}
            yield {"type": "tool_call", "id": "call_2", "name": "list_recipes", "arguments": {}}
        elif step == 2:
            yield {"type": "tool_call", "id": "call_3", "name": "inspect_project", "arguments": {}}
        else:
            yield {"type": "text_delta", "content": "done"}
        yield {"type": "done"}

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, "hi")]

    original = agent_core.stream_llm_with_tools
    agent_core.stream_llm_with_tools = fake_stream_llm_with_tools
    try:
        events = asyncio.run(collect())
    finally:
        agent_core.stream_llm_with_tools = original

    assert live_contexts == ["SNAPSHOT", UNCHANGED_CONTEXT, UNCHANGED_CONTEXT]
    assert executor.build_calls == 1
    assert events[-1]["message"] == "done"


def test_refresh_context_rebuilds_snapshot():
    executor = StubExecutor()
    live_contexts = []

    async def fake_stream_llm_with_tools(**kwargs):
        live_contexts.append(kwargs.get("live_context"))
        step = len(live_contexts)
        if step == 1:
            yield {"type": "tool_call", "id": "call_1", "name": "fetch_url", "arguments": {}}
        else:
            yield {"type": "text_delta", "content": "done"}
        yield {"type": "done"}

    async def execute_tool(self, tool_name, arguments, **kwargs):
        self.tool_calls.append(tool_name)
        return ToolCallResult(
            observation={"status": "ok"},
            refresh_context=(tool_name == "fetch_url"),
        )

    executor.execute_tool = execute_tool.__get__(executor)

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, "hi")]

    original = agent_core.stream_llm_with_tools
    agent_core.stream_llm_with_tools = fake_stream_llm_with_tools
    try:
        asyncio.run(collect())
    finally:
        agent_core.stream_llm_with_tools = original

    # Step 1: initial snapshot; step 2: refreshed snapshot (rebuild triggered).
    assert live_contexts[0] == "SNAPSHOT"
    assert live_contexts[1] == "SNAPSHOT"
    assert executor.build_calls == 2


def test_parallel_eligible_reads_run_concurrently():
    executor = StubExecutor()
    executor.parallel_eligible = lambda name: name == "read_file"

    async def fake_stream_llm_with_tools(**kwargs):
        calls = getattr(fake_stream_llm_with_tools, "calls", 0) + 1
        fake_stream_llm_with_tools.calls = calls
        if calls == 1:
            yield {"type": "tool_call", "id": "call_1", "name": "read_file", "arguments": {}}
            yield {"type": "tool_call", "id": "call_2", "name": "read_file", "arguments": {}}
        else:
            yield {"type": "text_delta", "content": "done"}
        yield {"type": "done"}

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, "hi")]

    original = agent_core.stream_llm_with_tools
    agent_core.stream_llm_with_tools = fake_stream_llm_with_tools
    try:
        asyncio.run(collect())
    finally:
        agent_core.stream_llm_with_tools = original

    assert executor.max_active == 2
    assert [name for name, _ in executor.tool_calls] == ["read_file", "read_file"]


def test_partition_parallel_groups():
    calls = [
        {"id": "1", "name": "read_file", "arguments": {}},
        {"id": "2", "name": "list_files", "arguments": {}},
        {"id": "3", "name": "inspect_project", "arguments": {}},
        {"id": "4", "name": "read_results", "arguments": {}},
    ]
    executor = StubExecutor()
    executor.parallel_eligible = lambda name: name in {"read_file", "list_files", "read_results"}
    groups = agent_core._partition_parallel_groups(calls, executor)
    assert [[tc["name"] for tc in group] for group in groups] == [
        ["read_file", "list_files"],
        ["inspect_project"],
        ["read_results"],
    ]


def test_classify_intent_uses_configured_token_budget(monkeypatch):
    captured = {}

    async def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return '{"intent": "conceptual"}'

    monkeypatch.setattr(settings, "fast_path_judge_max_tokens", 512)
    monkeypatch.setattr(settings, "fast_path_judge_reasoning_effort", "low")
    monkeypatch.setattr("app.services.intent_fastpath.call_llm", fake_call_llm)
    assert asyncio.run(classify_intent("What is a p-value?")) == "conceptual"
    assert captured["max_tokens"] == 512
    assert captured["reasoning_effort"] == "low"


def test_classify_intent_parses_judge_response(monkeypatch):
    async def fake_call_llm(**kwargs):
        return '{"intent": "needs_knowledge"}'

    monkeypatch.setattr("app.services.intent_fastpath.call_llm", fake_call_llm)
    assert asyncio.run(classify_intent("How does normalization work?")) == "needs_knowledge"


def test_classify_intent_falls_back_to_tool_loop_on_failure(monkeypatch):
    async def failing_call_llm(**kwargs):
        raise RuntimeError("judge unavailable")

    monkeypatch.setattr("app.services.intent_fastpath.call_llm", failing_call_llm)
    assert asyncio.run(classify_intent("What is a p-value?")) == "needs_tools"


def test_classify_intent_invalid_output_routes_to_tool_loop(monkeypatch):
    async def garbage_call_llm(**kwargs):
        return "sure, I can help with that."

    monkeypatch.setattr("app.services.intent_fastpath.call_llm", garbage_call_llm)
    assert asyncio.run(classify_intent("What is a p-value?")) == "needs_tools"


def test_classify_intent_disabled(monkeypatch):
    monkeypatch.setattr(settings, "fast_path_judge_enabled", False)
    assert asyncio.run(classify_intent("What is a p-value?")) == "needs_tools"


def test_friendly_tool_label_never_leaks_identifiers():
    from app.services.agent_core import friendly_tool_label

    assert friendly_tool_label("run_r_cell") == "Running R cell"
    assert friendly_tool_label("list_files") == "Listing workspace files"
    assert friendly_tool_label("search_bioc_books") == "Searching Bioconductor books"
    assert friendly_tool_label("update_recipe_parameters") == "Updating recipe parameters"
    label = friendly_tool_label("future_mystery_tool")
    assert label == "Future mystery tool"
    assert "_" not in label


def test_gray_zone_question_passes_gate_and_is_judged():
    # No keyword filtering: every message passes the sanity check; the judge
    # routes semantically.
    assert is_simple_question("How does log normalization affect my data?")
    assert is_simple_question("What is a p-value?")
    assert is_simple_question("Calculate the mean of 1, 2, and 3")


def test_gray_zone_conceptual_routes_to_fast_path():
    executor = StubExecutor()
    executor.use_fast_path = lambda message: True

    async def judge(self, message):
        assert message == "How does log normalization affect my data?"
        return "conceptual"

    executor.judge_intent = judge.__get__(executor)

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, "How does log normalization affect my data?")]

    events = asyncio.run(collect())
    assert events[-1]["message"] == "fast answer"
    assert executor.tool_calls == []


def _fake_knowledge_handler(arguments):
    return {
        "status": "ok",
        "matches": [
            {
                "book_title": "OSCA",
                "title": "Basic data structures",
                "prose": "Alpha diversity summarizes within-sample richness.",
                "citation": "OSCA, Basic data structures (RELEASE_3_23, abc123)",
            }
        ],
    }


def test_workspace_fast_path_seeds_knowledge_for_conceptual_intent():
    from app.services.workspace_agent import WorkspaceAgentExecutor

    executor = WorkspaceAgentExecutor.__new__(WorkspaceAgentExecutor)
    executor.knowledge_search_handler = _fake_knowledge_handler
    seed = executor._knowledge_seed("What is alpha diversity?")
    assert seed is not None
    assert "OSCA" in seed
    assert "RELEASE_3_23" in seed


def test_note_fast_path_seeds_knowledge_for_conceptual_intent(monkeypatch):
    from app.services.note_agent import NoteAgentExecutor

    captured = {}

    async def fake_stream_simple_answer(message, knowledge_context=None):
        captured["knowledge_context"] = knowledge_context
        yield {"type": "token", "token": "ok"}
        yield {"type": "final", "message": "ok", "fast": True}

    monkeypatch.setattr("app.services.intent_fastpath.stream_simple_answer", fake_stream_simple_answer)

    executor = NoteAgentExecutor(
        message="What is alpha diversity?",
        cells=[],
        context={},
        knowledge_search_handler=_fake_knowledge_handler,
    )

    async def collect():
        return [event async for event in executor.fast_path_events("What is alpha diversity?", intent="conceptual")]

    events = asyncio.run(collect())
    assert captured["knowledge_context"] is not None
    assert "OSCA" in captured["knowledge_context"]
    assert events[-1]["fast"] is True


def test_fast_path_no_knowledge_handler_answers_plain(monkeypatch):
    from app.services.note_agent import NoteAgentExecutor

    captured = {}

    async def fake_stream_simple_answer(message, knowledge_context=None):
        captured["knowledge_context"] = knowledge_context
        yield {"type": "final", "message": "ok", "fast": True}

    monkeypatch.setattr("app.services.intent_fastpath.stream_simple_answer", fake_stream_simple_answer)

    executor = NoteAgentExecutor(message="What is a p-value?", cells=[], context={})
    asyncio.run(_drain(executor.fast_path_events("What is a p-value?")))
    assert captured["knowledge_context"] is None


def test_fast_path_knowledge_failure_does_not_break_answer(monkeypatch):
    from app.services.note_agent import NoteAgentExecutor

    captured = {}

    async def fake_stream_simple_answer(message, knowledge_context=None):
        captured["knowledge_context"] = knowledge_context
        yield {"type": "final", "message": "ok", "fast": True}

    monkeypatch.setattr("app.services.intent_fastpath.stream_simple_answer", fake_stream_simple_answer)

    def failing_handler(arguments):
        raise RuntimeError("knowledge down")

    executor = NoteAgentExecutor(
        message="What is a p-value?",
        cells=[],
        context={},
        knowledge_search_handler=failing_handler,
    )
    asyncio.run(_drain(executor.fast_path_events("What is a p-value?")))
    assert captured["knowledge_context"] is None


async def _drain(stream):
    return [event async for event in stream]
