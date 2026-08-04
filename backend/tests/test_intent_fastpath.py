"""Fast-intent path: heuristic gating and core-loop integration."""

from __future__ import annotations

import pytest

from app.services import agent_core, intent_fastpath
from app.services.intent_fastpath import is_simple_question


def test_simple_questions_qualify():
    assert is_simple_question("What is a p-value?")
    assert is_simple_question("What is Shannon diversity and when is it appropriate to use?")
    assert is_simple_question("Why do we use multiple testing correction?")
    assert is_simple_question("Explain the difference between mean and median.")
    assert is_simple_question("How does ordination work?")
    assert is_simple_question("What's a permutation test?")


def test_tool_questions_do_not_qualify():
    assert not is_simple_question("Which group has higher Shannon diversity?")
    assert not is_simple_question("Calculate the mean of 1, 2, and 3")
    assert not is_simple_question("How do I compute alpha diversity?")
    assert not is_simple_question("Compare the two groups in my study")
    assert not is_simple_question("Run a PERMANOVA on my samples")
    assert not is_simple_question("Analyze my data")
    assert not is_simple_question("What does this CSV contain?")
    assert not is_simple_question("Can you build the report?")
    assert not is_simple_question("How do I install the phyloseq package?")
    assert not is_simple_question("Import the GlobalPatterns dataset and plot alpha diversity")
    assert not is_simple_question("Why is my analysis taking so long?")


def test_non_questions_and_long_messages_do_not_qualify():
    assert not is_simple_question("hello")
    assert not is_simple_question("Continue")
    assert not is_simple_question("What is a p-value? " + "x" * 250)


def test_fast_path_model_resolution(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "fast_path_model", "")
    monkeypatch.setattr(settings, "llm_provider", "qwen")
    assert intent_fastpath.fast_path_model() == "qwen-plus"
    monkeypatch.setattr(settings, "llm_provider", "groq")
    assert intent_fastpath.fast_path_model() == "llama-3.3-70b-versatile"
    monkeypatch.setattr(settings, "fast_path_model", "qwen-plus-fast")
    assert intent_fastpath.fast_path_model() == "qwen-plus-fast"


def test_fast_path_short_circuits_the_loop():
    class StubExecutor:
        max_steps = 5
        max_tokens = 100
        max_tool_chars = 1000
        system_prompt = ""
        tools = []
        use_retry_guard = False
        cancelled_message = "cancelled"
        default_final_message = "default"
        max_steps_message = "max"

        def initial_events(self, message):
            return [], False

        def build_messages(self, message):
            return [{"role": "user", "content": message}]

        def build_live_context(self):
            return ""

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

        async def execute_tool(self, *args, **kwargs):
            raise AssertionError("fast path must not execute tools")

        async def legacy_llm_step(self, messages, *, step):
            return None

        def use_fast_path(self, message):
            return True

        async def fast_path_events(self, message):
            yield {"type": "token", "token": "hi "}
            yield {"type": "final", "message": "hi there", "fast": True}

    async def collect():
        return [event async for event in agent_core.run_agent_loop(StubExecutor(), "What is a p-value?")]

    events = collect()
    import asyncio

    events = asyncio.run(events)
    assert [e["type"] for e in events] == ["token", "final"]
    assert events[-1]["message"] == "hi there"
    assert events[-1]["fast"] is True
