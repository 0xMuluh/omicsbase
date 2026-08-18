"""Native agent loop mechanics, scripted at the provider boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_test_helpers import stream_turns, text_turn, tool_turn
from app.services import agent_loop
from app.services.agent_core import ToolCallResult


class StubExecutor:
    """Minimal executor contract for driving the native loop."""

    def __init__(self, *, tools_response: dict[str, Any] | None = None, results: list[ToolCallResult] | None = None):
        self.tools = [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}]
        self.system_prompt = "You are a test executor."
        self.max_steps = 6
        self.max_tokens = 1000
        self.default_final_message = "no answer"
        self.cancelled_message = "cancelled"
        self.use_retry_guard = True
        self.emit_tokens = True
        self.use_fast_path = lambda _message: False
        self.results = results if results is not None else []
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.budget_profile = "agent"
        self.llm_provider_override = None
        self.llm_model_override = None

    def initial_events(self, message: str) -> tuple[list[dict], bool]:
        return [{"type": "status", "status": "thinking", "message": "thinking"}], False

    def build_messages(self, message: str) -> list[dict]:
        return [{"role": "user", "content": message}]

    def build_live_context(self) -> str:
        return "live-context"

    def final_event(self, message: str) -> dict:
        return {"type": "final", "message": message, "memory_updates": []}

    def fallback_events(self, exc: Exception) -> list[dict]:
        return [{"type": "final", "message": f"fallback: {exc}"}]

    def tool_completed_event(self, tool, tool_call_id, arguments, status, summary, step) -> dict:
        return {
            "type": "tool_completed",
            "tool": tool,
            "tool_call_id": tool_call_id,
            "status": status,
            "summary": summary,
            "step": step,
        }

    def summary_for(self, tool_name: str, observation: dict) -> str:
        return f"summary:{tool_name}"

    def tool_spec(self, tool_name: str):
        from app.services.tool_specs import TOOL_REGISTRY

        return TOOL_REGISTRY.get(tool_name)

    def parallel_eligible(self, tool_name: str) -> bool:
        return False

    async def execute_tool(self, tool_name, arguments, *, step, tool_call_id, persisted_arguments, step_text):
        self.executed.append((tool_name, dict(arguments)))
        if self.results:
            return self.results.pop(0)
        return ToolCallResult(observation={"status": "ok", "value": 1})


def _run(executor, message="hello"):
    async def collect():
        return [event async for event in agent_loop.stream_agent_turn(executor, message)]

    return asyncio.run(collect())


def test_text_only_turn_emits_tokens_and_final(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.stream_llm_with_tools", stream_turns([text_turn("All done.")])
    )
    executor = StubExecutor()
    events = _run(executor)
    tokens = "".join(e.get("token", "") for e in events if e["type"] == "token")
    finals = [e for e in events if e["type"] == "final"]
    assert tokens == "All done."
    assert finals and finals[-1]["message"] == "All done."
    assert any(e["type"] == "budget" for e in events)
    assert any(e["type"] == "usage_summary" for e in events)


def test_tool_call_feeds_observation_back(monkeypatch):
    captured: list[dict[str, Any]] = []

    async def fake_stream(**kwargs):
        captured.append(kwargs)
        for event in next(captured_turns):
            yield event

    captured_turns = iter([tool_turn("ping", {"x": 1}), text_turn("Finished after tool.")])
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", fake_stream)
    executor = StubExecutor()
    events = _run(executor)

    assert executor.executed == [("ping", {"x": 1})]
    assert any(e["type"] == "tool_started" and e["tool"] == "ping" for e in events)
    assert any(e["type"] == "tool_completed" and e["status"] == "ok" for e in events)
    # Second provider call must include the assistant tool_call + tool observation.
    second = captured[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2].get("tool_calls")
    assert second[-1]["role"] == "tool" and '"status"' in second[-1]["content"]
    assert [e for e in events if e["type"] == "final"][-1]["message"] == "Finished after tool."


def test_end_turn_result_stops_loop(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.stream_llm_with_tools",
        stream_turns([tool_turn("ping"), text_turn("never reached")]),
    )
    executor = StubExecutor(
        results=[ToolCallResult(
            observation={"status": "ok"},
            end_turn=True,
            final_event={"type": "final", "message": "stopped early", "memory_updates": []},
        )]
    )
    events = _run(executor)
    assert [e for e in events if e["type"] == "final"][-1]["message"] == "stopped early"


def test_llm_call_budget_stops_with_explanation(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.stream_llm_with_tools",
        stream_turns([tool_turn("ping"), text_turn("x")]),
    )
    executor = StubExecutor()

    original = agent_loop.TurnBudget.from_settings

    def one_call_budget(*_args, **_kwargs):
        budget = original(profile="agent")
        budget.max_llm_calls = 1
        return budget

    monkeypatch.setattr(agent_loop, "TurnBudget", type("PatchedTurnBudget", (), {"from_settings": staticmethod(one_call_budget)}))
    events = _run(executor)
    finals = [e for e in events if e["type"] == "final"]
    assert finals and "budget" in finals[-1]["message"].lower()


def test_provider_failure_yields_fallback(monkeypatch):
    async def failing_stream(**_kwargs):
        raise RuntimeError("provider down")
        yield {"type": "done"}  # pragma: no cover

    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", failing_stream)
    executor = StubExecutor()
    events = _run(executor)
    assert [e for e in events if e["type"] == "final"][-1]["message"] == "fallback: provider down"


def test_step_ceiling_reports_clearly(monkeypatch):
    # Every turn requests a tool; the loop must stop at max_steps.
    monkeypatch.setattr(
        "app.services.llm.stream_llm_with_tools",
        stream_turns([tool_turn("ping")] * 10),
    )
    executor = StubExecutor()
    events = _run(executor)
    finals = [e for e in events if e["type"] == "final"]
    assert finals and "step" in finals[-1]["message"].lower()
    assert len(executor.executed) == executor.max_steps


def test_usage_events_recorded_and_forwarded(monkeypatch):
    turn = [
        {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
        {"type": "text_delta", "content": "hi"},
        {"type": "done"},
    ]
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", stream_turns([turn]))
    events = _run(StubExecutor())
    usage = [e for e in events if e["type"] == "usage"]
    summary = [e for e in events if e["type"] == "usage_summary"][-1]["usage"]
    assert usage and usage[0]["usage"]["total_tokens"] == 15
    assert summary["responses"] == 1 and summary["total_tokens"] == 15


def test_failed_non_idempotent_retry_is_allowed_then_success_blocks(monkeypatch):
    """Render → fail → fix → re-render must never hit the duplicate guard."""
    calls = []

    def make_turns():
        yield [
            {"type": "tool_call", "id": "r1", "name": "render_report", "arguments": {}},
            {"type": "done"},
        ]
        yield [
            {"type": "tool_call", "id": "r2", "name": "render_report", "arguments": {}},
            {"type": "done"},
        ]
        yield [
            {"type": "tool_call", "id": "r3", "name": "render_report", "arguments": {}},
            {"type": "done"},
        ]
        yield text_turn("Rendered after repairs.")

    async def fake_stream(**kwargs):
        for event in next(calls_iter):
            yield event

    calls_iter = iter(make_turns())
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", fake_stream)
    executor = StubExecutor()

    async def render_tool(name, arguments, **kwargs):
        calls.append(name)
        result_index = len(calls)
        status = "error" if result_index <= 2 else "completed"
        return ToolCallResult(observation={"status": status, "render_status": status})

    executor.execute_tool = render_tool
    executor.tool_spec = lambda name: __import__("app.services.tool_specs", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY.get("render_report")
    events = _run(executor)
    statuses = [e.get("status") for e in events if e["type"] == "tool_completed"]
    assert len(calls) == 3, "all three render attempts must execute"
    assert "duplicate" not in str(statuses).lower()
    assert statuses.count("error") == 2 and statuses.count("ok") == 1


def test_duplicate_after_success_is_blocked(monkeypatch):
    calls = []

    def make_turns():
        yield [
            {"type": "tool_call", "id": "f1", "name": "fetch_url", "arguments": {"url": "https://x/y.csv"}},
            {"type": "done"},
        ]
        yield [
            {"type": "tool_call", "id": "f2", "name": "fetch_url", "arguments": {"url": "https://x/y.csv"}},
            {"type": "done"},
        ]

    async def fake_stream(**kwargs):
        for event in next(calls_iter):
            yield event

    calls_iter = iter(make_turns())
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", fake_stream)
    executor = StubExecutor()

    async def fetch_tool(name, arguments, **kwargs):
        calls.append(arguments)
        return ToolCallResult(observation={"status": "ok"})

    executor.execute_tool = fetch_tool
    from app.services.tool_specs import TOOL_REGISTRY

    executor.tool_spec = lambda name: TOOL_REGISTRY.get("fetch_url")
    _run(executor)
    assert len(calls) == 1, "second identical successful fetch must be blocked"


def test_run_r_script_routes_to_dedicated_action(tmp_path, monkeypatch):
    """run_r_script must never fall through to the generic inspect dispatcher."""
    import asyncio
    from types import SimpleNamespace

    from app.services import workspace_agent

    project = SimpleNamespace(
        project_dir=str(tmp_path),
        status="planning",
        agent_memory={},
        agent_actions=None,
        study_manifest={},
        analysis_plan=None,
        name="p",
    )
    request = SimpleNamespace(
        message="run the prep script",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path=None,
        chat_mode="build",
    )
    script = tmp_path / "code" / "prep.R"
    script.parent.mkdir(parents=True)
    script.write_text("x <- 1\n")

    def fake_sync_runner(cmd, cwd, timeout=600):
        return True, "[1] 1"

    monkeypatch.setattr("app.services.runner.run_command_sync", fake_sync_runner)
    executor = workspace_agent.WorkspaceAgentExecutor(
        project=project,
        request=request,
        persisted_messages=[],
    )
    result = asyncio.run(executor.execute_tool(
        "run_r_script",
        {"path": "code/prep.R"},
        step=1,
        tool_call_id="rs-1",
        persisted_arguments={"path": "code/prep.R"},
        step_text="",
    ))
    assert result.observation["status"] == "ok", result.observation
    assert result.observation["output_tail"].strip().endswith("[1] 1")


def test_consecutive_failure_breaker_ends_turn(monkeypatch):
    """Six consecutive failures of one tool with varying args end the turn."""
    attempts = []

    def make_turns():
        for i in range(10):
            yield [
                {"type": "tool_call", "id": f"s{i}", "name": "set_plan", "arguments": {"plan": {"attempt": i}}},
                {"type": "done"},
            ]

    async def fake_stream(**kwargs):
        for event in next(calls_iter):
            yield event

    calls_iter = iter(make_turns())
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", fake_stream)
    executor = StubExecutor()

    async def failing_tool(name, arguments, **kwargs):
        attempts.append(arguments)
        return ToolCallResult(observation={"status": "error", "error": "not an object"})

    executor.execute_tool = failing_tool
    events = _run(executor)
    finals = [e for e in events if e["type"] == "final"]
    assert len(attempts) == 6, "breaker must stop the turn at six consecutive failures"
    assert "failed 6 times in a row" in finals[-1]["message"]


def test_breaker_resets_on_success(monkeypatch):
    calls = []

    def make_turns():
        for i in range(4):
            yield [
                {"type": "tool_call", "id": f"a{i}", "name": "ping", "arguments": {}},
                {"type": "done"},
            ]
        yield text_turn("done after alternating outcomes")

    async def fake_stream(**kwargs):
        for event in next(calls_iter):
            yield event

    calls_iter = iter(make_turns())
    monkeypatch.setattr("app.services.llm.stream_llm_with_tools", fake_stream)
    executor = StubExecutor()

    async def alternating_tool(name, arguments, **kwargs):
        calls.append(name)
        ok = len(calls) % 2 == 0
        return ToolCallResult(observation={"status": "ok" if ok else "error", "error": None if ok else "boom"})

    executor.execute_tool = alternating_tool
    events = _run(executor)
    assert len(calls) == 4, "alternating failures never trip the breaker"
    assert [e for e in events if e["type"] == "final"][-1]["message"] == "done after alternating outcomes"
