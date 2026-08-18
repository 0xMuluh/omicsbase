from __future__ import annotations

from app.services.agent_core import TurnBudget
from app.services.note_agent import NOTE_AGENT_SYSTEM_PROMPT, NoteAgentExecutor
from app.services.context_budget import bounded_json
from app.services.tool_specs import NOTE_TOOL_SPECS, TOOL_REGISTRY
from app.services.workspace_agent import WorkspaceAgentExecutor, _read_results


def test_registry_has_strict_edit_schema_and_inline_render():
    edit = TOOL_REGISTRY.require("edit_project", lens="workspace")
    assert edit.parameters["additionalProperties"] is False
    assert set(edit.parameters["properties"]) == {"mode", "path", "search", "replace", "content", "patch", "edits", "allow_multiple", "reason", "expected_sha256", "approval"}
    assert len(edit.parameters["oneOf"]) == 4
    assert {branch["properties"]["mode"]["const"] for branch in edit.parameters["oneOf"]} == {"search_replace", "content", "patch", "batch"}
    assert all(branch["additionalProperties"] is False for branch in edit.parameters["oneOf"])
    assert "instruction" not in str(edit.parameters)
    render = TOOL_REGISTRY.require("render_report", lens="workspace")
    assert render.kind == "inline" and render.risk == "execute"
    assert "render_report" in {item.name for item in TOOL_REGISTRY.advertised(lens="workspace", capabilities={"report_execution"})}
    assert "repair_report" not in {item.name for item in TOOL_REGISTRY.advertised(lens="workspace", capabilities={"report_execution"})}


def test_registry_exposes_explicit_result_resume_and_undo_contracts():
    read_results = TOOL_REGISTRY.require("read_results", lens="workspace")
    assert read_results.parameters["required"] == ["path"]
    render = TOOL_REGISTRY.require("render_report", lens="workspace")
    assert render.kind == "inline" and render.risk == "execute"
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
    stage = TOOL_REGISTRY.require("stage_report_pack", lens="workspace")
    assert stage.kind == "inline" and stage.capability == "report_execution"
    assert TOOL_REGISTRY.require("render_report", lens="workspace").effective_budget == 4
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


def test_note_contract_does_not_prescribe_notebook_choreography():
    assert "For each logical analysis step" not in NOTE_AGENT_SYSTEM_PROMPT
    assert "Do not impose a fixed" in NOTE_AGENT_SYSTEM_PROMPT
    assert "Never claim a computed value" in NOTE_AGENT_SYSTEM_PROMPT
    assert "search_bioc_books" in NOTE_AGENT_SYSTEM_PROMPT
    assert "queued or running execution is not a result" in TOOL_REGISTRY.require("run_r_cell", lens="note").description
    assert "2-4 sentences" not in TOOL_REGISTRY.require("add_note", lens="note").parameters["properties"]["text"]["description"]




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
        "max_input_tokens": 80000,
        "max_total_tokens": 100000,
        "llm_calls": 0,
        "generated_tokens": 0,
        "retrieved_chars": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
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
