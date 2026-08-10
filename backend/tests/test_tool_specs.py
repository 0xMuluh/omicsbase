from __future__ import annotations

import asyncio

from app.services import agent_core
from app.services.agent_core import ToolCallResult, TurnBudget
from app.services.note_agent import NoteAgentExecutor
from app.services.context_budget import bounded_json
from app.services.tool_specs import NOTE_TOOL_SPECS, TOOL_REGISTRY
from app.services.workspace_agent import WorkspaceAgentExecutor, _read_results


def test_registry_has_strict_edit_schema_and_hidden_render_alias():
    edit = TOOL_REGISTRY.require("edit_project", lens="workspace")
    assert edit.parameters["additionalProperties"] is False
    assert set(edit.parameters["properties"]) == {"mode", "path", "search", "replace", "content", "patch", "edits", "allow_multiple", "reason", "expected_sha256", "approval"}
    assert len(edit.parameters["oneOf"]) == 4
    assert {branch["properties"]["mode"]["const"] for branch in edit.parameters["oneOf"]} == {"search_replace", "content", "patch", "batch"}
    assert all(branch["additionalProperties"] is False for branch in edit.parameters["oneOf"])
    assert "instruction" not in str(edit.parameters)
    assert TOOL_REGISTRY.require("repair_report", lens="workspace").advertised is False
    assert "render_report" in {item.name for item in TOOL_REGISTRY.advertised(lens="workspace", capabilities={"report_execution"})}
    assert "repair_report" not in {item.name for item in TOOL_REGISTRY.advertised(lens="workspace", capabilities={"report_execution"})}


def test_registry_exposes_explicit_result_resume_and_undo_contracts():
    read_results = TOOL_REGISTRY.require("read_results", lens="workspace")
    assert read_results.parameters["required"] == ["path"]
    run_analysis = TOOL_REGISTRY.require("run_analysis", lens="workspace")
    assert run_analysis.parameters["properties"]["resume_from_checkpoint"]["default"] is True
    undo = TOOL_REGISTRY.require("undo_project_edit", lens="workspace")
    assert undo.parameters["required"] == ["transaction_id"]
    assert undo.idempotency == "non_idempotent"


def test_registry_tracks_note_lens_and_capability_metadata():
    assert {item.name for item in NOTE_TOOL_SPECS} >= {"run_r_cell", "promote_to_workspace"}
    assert TOOL_REGISTRY.require("run_r_cell", lens="note").idempotency == "non_idempotent"
    assert TOOL_REGISTRY.require("run_recipe", lens="workspace").capability == "legacy_recipe"
    promote = TOOL_REGISTRY.require("promote_to_workspace", lens="note")
    assert promote.parameters["properties"]["strategy"]["default"] == "create_only"
    assert "base_sha256" in promote.parameters["properties"]


def test_registry_is_runtime_policy_source_for_lenses():
    plan = TOOL_REGISTRY.require("plan_analysis", lens="workspace")
    run = TOOL_REGISTRY.require("run_analysis", lens="workspace")
    assert plan.intent == "pipeline"
    assert run.intent == "pipeline"
    assert run.effective_budget == 4
    assert TOOL_REGISTRY.require("inspect_project", lens="workspace").parallel is False
    assert TOOL_REGISTRY.require("search_workspace", lens="workspace").parallel is True

    workspace = object.__new__(WorkspaceAgentExecutor)
    assert workspace.parallel_eligible("read_file") is True
    assert workspace.parallel_eligible("inspect_project") is False
    assert workspace.tool_idempotency("edit_project") == "non_idempotent"

    note = NoteAgentExecutor(message="inspect", cells=[], context={})
    assert note.parallel_eligible("inspect_note") is False
    assert note.tool_idempotency("add_note") == "non_idempotent"
    assert note.tool_idempotency("run_r_cell") == "non_idempotent"
def test_read_results_requires_named_artifact_and_reports_available_paths(tmp_path):
    project = type("Project", (), {"project_dir": str(tmp_path)})()
    result = tmp_path / "output" / "results" / "answer.csv"
    result.parent.mkdir(parents=True)
    result.write_text("group,value\nA,2\n")

    missing = _read_results(project, "")
    assert missing["status"] == "error"
    assert missing["available_artifacts"] == ["output/results/answer.csv"]
    named = _read_results(project, "output/results/answer.csv")
    assert named["status"] == "ok"
    assert named["rows"] == [{"group": "A", "value": "2"}]


class _DuplicateExecutor:
    max_steps = 2
    max_tokens = 100
    max_tool_chars = 1000
    system_prompt = ""
    tools = []
    use_retry_guard = False
    llm_provider_override = None
    llm_model_override = None
    cancelled_message = "cancelled"
    default_final_message = "done"
    max_steps_message = "max"

    def __init__(self):
        self.calls = 0

    def initial_events(self, message): return [], False
    def build_messages(self, message): return [{"role": "user", "content": message}]
    def build_live_context(self): return ""
    def fallback_events(self, exc): return [{"type": "final", "message": str(exc)}]
    def final_event(self, message): return {"type": "final", "message": message}
    def max_steps_events(self): return [{"type": "final", "message": "max"}]
    def tool_completed_event(self, *args, **kwargs): return {"type": "tool_completed"}
    def summary_for(self, name, observation): return str(observation.get("status"))
    def use_fast_path(self, message): return False
    async def legacy_llm_step(self, messages, *, step): return None
    def parallel_eligible(self, name): return False
    def tool_idempotency(self, name): return "non_idempotent" if name == "run_r_cell" else "read_only"
    async def execute_tool(self, name, arguments, **kwargs):
        self.calls += 1
        return ToolCallResult(observation={"status": "queued"})


def test_duplicate_non_idempotent_call_is_suppressed(monkeypatch):
    executor = _DuplicateExecutor()

    async def fake_stream(**kwargs):
        yield {"type": "tool_call", "id": "one", "name": "run_r_cell", "arguments": {"code": "1+1"}}
        yield {"type": "tool_call", "id": "two", "name": "run_r_cell", "arguments": {"code": "1+1"}}
        yield {"type": "done"}

    monkeypatch.setattr(agent_core, "stream_llm_with_tools", fake_stream)
    events = asyncio.run(_collect(executor))
    assert executor.calls == 1
    # The duplicate is observable in the tool-started telemetry and the tool
    # result fed back to the model.
    assert any("Duplicate call suppressed" in str(event) for event in events)


async def _collect(executor):
    return [event async for event in agent_core.run_agent_loop(executor, "run a cell")]


def test_turn_budget_enforces_units_calls_and_mutations():
    budget = TurnBudget(max_units=5, max_tool_calls=2, max_mutations=1)

    assert budget.try_consume_tool(cost=1, mutating=False) == (True, None)
    assert budget.try_consume_tool(cost=3, mutating=True) == (True, None)
    allowed, reason = budget.try_consume_tool(cost=1, mutating=False)
    assert allowed is False
    assert "tool calls" in reason
    assert budget.snapshot() == {
        "units_used": 4,
        "max_units": 5,
        "tool_calls": 2,
        "max_tool_calls": 2,
        "mutation_count": 1,
        "max_mutations": 1,
        "max_llm_calls": 8,
        "max_generated_tokens": 20000,
        "max_retrieved_chars": 80000,
        "llm_calls": 0,
        "generated_tokens": 0,
        "retrieved_chars": 0,
    }


def test_bounded_tool_observation_is_always_valid_json():
    rendered = bounded_json(
        {"status": "ok", "data": "x" * 5000, "path": "output/results/result.csv"},
        180,
        priority_keys=("status", "path", "data"),
    )

    parsed = __import__("json").loads(rendered)
    assert parsed["status"] == "ok"
    assert len(rendered) <= 180


def test_turn_budget_enforces_expensive_resource_caps():
    budget = TurnBudget(
        max_units=5,
        max_tool_calls=5,
        max_mutations=2,
        max_llm_calls=1,
        max_generated_tokens=2,
        max_retrieved_chars=10,
    )

    assert budget.try_record_llm_call() == (True, None)
    allowed, reason = budget.try_record_llm_call()
    assert allowed is False
    assert "LLM calls" in reason
    assert budget.record_generated("12345678") is True
    assert budget.record_generated("x") is False
    assert budget.record_retrieved("1234567890") is True
    assert budget.record_retrieved("x") is False
