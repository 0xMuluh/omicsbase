"""Fast-intent path: heuristic gating and core-loop integration."""

from __future__ import annotations

import pytest

from app.services import agent_core, intent_fastpath
from app.services.intent_fastpath import deterministic_intent, is_simple_question


def test_simple_questions_qualify():
    assert is_simple_question("What is a p-value?")
    assert is_simple_question("What is Shannon diversity and when is it appropriate to use?")
    assert is_simple_question("Why do we use multiple testing correction?")
    assert is_simple_question("Explain the difference between mean and median.")
    assert is_simple_question("How does ordination work?")
    assert is_simple_question("What's a permutation test?")


def test_conceptual_questions_mentioning_data_still_qualify():
    # No keyword filtering: words like "data" or "analysis" never reject —
    # the LLM judge decides semantically.
    assert is_simple_question("How does log normalization affect my data?")
    assert is_simple_question("Why is my analysis taking so long?")
    assert is_simple_question("How do I compute alpha diversity?")
    assert is_simple_question("Which group has higher Shannon diversity?")
    assert is_simple_question("What does this CSV contain?")
    assert is_simple_question("Can you build the report?")
    assert is_simple_question("How do I install the phyloseq package?")
    assert is_simple_question("What is a p-value? " + "x" * 250)


def test_commands_and_file_references_also_qualify():
    # Even unambiguous tool commands pass the sanity check; the judge routes
    # them to the tool loop.
    assert is_simple_question("Calculate the mean of 1, 2, and 3")
    assert is_simple_question("Compare the two groups in my study")
    assert is_simple_question("Run a PERMANOVA on my samples")
    assert is_simple_question("Analyze my data")
    assert is_simple_question("Import the GlobalPatterns dataset and plot alpha diversity")
    assert is_simple_question("Summarize the results by group")
    assert is_simple_question("Read data.csv")
    assert is_simple_question("Render the report.qmd")
    assert is_simple_question("Continue")


def test_blank_and_absurdly_long_messages_do_not_qualify():
    assert not is_simple_question("")
    assert not is_simple_question("What is a p-value? " + "x" * 2000)


def test_deterministic_gate_handles_clear_cases_and_defers_ambiguity():
    assert deterministic_intent("What is a p-value?", lens="workspace") == "conceptual"
    assert deterministic_intent(
        "Why?",
        lens="workspace",
        selected_resource="output/results/permanova.csv",
    ) == "needs_tools"
    assert deterministic_intent("Run the calculation", lens="note") == "needs_tools"
    assert deterministic_intent("How do I compute alpha diversity?", lens="note") == "needs_tools"
    assert deterministic_intent("Why?", lens="workspace", active_job_status="failed") == "needs_tools"
    assert deterministic_intent("Which group is higher?", lens="workspace") is None
    assert deterministic_intent("Which ordination method works best for compositional data?", lens="workspace") == "needs_knowledge"


def test_demonstration_gate_grounds_method_examples_without_hijacking_actions():
    assert deterministic_intent("lets do a little example", lens="note") == "needs_knowledge"
    assert deterministic_intent("show me an example of a t-test", lens="workspace") == "needs_knowledge"
    assert deterministic_intent("what is FDR?", lens="workspace") == "needs_knowledge"
    assert deterministic_intent("run the analysis", lens="workspace") == "needs_tools"
    assert deterministic_intent("show me an example using my data", lens="note") == "needs_tools"
    assert deterministic_intent("show me this method", lens="note") == "needs_tools"


def test_fast_path_model_resolution(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "fast_path_model", "")
    monkeypatch.setattr(settings, "llm_provider", "qwen")
    assert intent_fastpath.fast_path_model() == "qwen3.7-plus-2026-05-26"
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

        def deterministic_intent(self, message):
            return "conceptual"

        async def judge_intent(self, message):
            raise AssertionError("deterministic route should skip the judge")

        async def fast_path_events(self, message, *, intent="conceptual"):
            assert intent == "conceptual"
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


def test_resolve_target(monkeypatch):
    from app.config import settings
    from app.services.llm import resolve_target

    monkeypatch.setattr(settings, "llm_agent_target", "qwen:qwen3.7-max")
    monkeypatch.setattr(settings, "llm_fast_target", "groq:llama-3.3-70b-versatile")
    monkeypatch.setattr(settings, "llm_planner_target", "")
    assert resolve_target("agent") == ("qwen", "qwen3.7-max")
    assert resolve_target("fast") == ("groq", "llama-3.3-70b-versatile")
    assert resolve_target("planner") == (None, None)
