from __future__ import annotations

from app.services.agent_core import TurnBudget
from app.services.note_agent import NOTE_AGENT_SYSTEM_PROMPT, NoteAgentExecutor
from app.services.context_budget import bounded_json
from app.services.tool_specs import NOTE_TOOL_SPECS, TOOL_REGISTRY


def test_registry_tracks_note_lens_and_capability_metadata():
    assert {item.name for item in NOTE_TOOL_SPECS} >= {"run_r_cell", "promote_to_workspace"}
    assert TOOL_REGISTRY.require("run_r_cell", lens="note").idempotency == "non_idempotent"
    promote = TOOL_REGISTRY.require("promote_to_workspace", lens="note")
    assert promote.parameters["properties"]["strategy"]["default"] == "create_only"
    assert "base_sha256" in promote.parameters["properties"]


def test_registry_is_runtime_policy_source_for_note_lens():
    note = NoteAgentExecutor(message="inspect", cells=[], context={})
    assert note.parallel_eligible("inspect_note") is False
    assert note.tool_idempotency("add_note") == "non_idempotent"
    assert note.tool_idempotency("run_r_cell") == "non_idempotent"


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
