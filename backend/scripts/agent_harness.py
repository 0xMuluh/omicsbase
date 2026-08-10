"""Deterministic routing and tool-budget smoke harness.

This harness intentionally makes no provider, network, or database calls. It
measures the safety-critical decisions that should remain stable even when an
LLM is unavailable: request routing, tool-contract coverage, and budget
accounting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.agent_core import TurnBudget
from app.services.context_budget import bounded_json
from app.services.intent_fastpath import deterministic_intent
from app.services.tool_specs import TOOL_REGISTRY

HARNESS_VERSION = "omicsbase-agent-harness-v1"

CASES: tuple[dict[str, Any], ...] = (
    {"name": "conceptual_definition", "message": "What is a p-value?", "lens": "workspace", "expected": "conceptual"},
    {"name": "workspace_question", "message": "Why is my analysis slow?", "lens": "workspace", "expected": None},
    {"name": "explicit_action", "message": "Run a PERMANOVA on my samples", "lens": "workspace", "expected": "needs_tools"},
    {"name": "continuation", "message": "Continue", "lens": "workspace", "expected": "needs_tools"},
    {"name": "note_follow_up", "message": "What does this output contain?", "lens": "note", "notebook_state": True, "expected": "needs_tools"},
    {"name": "method_selection", "message": "Which ordination method works best for compositional data?", "lens": "workspace", "expected": "needs_knowledge"},
)

REQUIRED_TOOLS = {
    "workspace": ("inspect_project", "read_file", "read_results", "inspect_table", "ask_user"),
    "note": ("inspect_note", "run_r_cell", "add_note"),
}


def _route(case: dict[str, Any]) -> str | None:
    return deterministic_intent(
        case["message"],
        lens=case["lens"],
        explicit_mutation=bool(case.get("explicit_mutation", False)),
        selected_resource=case.get("selected_resource"),
        selected_content_dirty=bool(case.get("selected_content_dirty", False)),
        active_job_status=case.get("active_job_status"),
        prior_tool_activity=bool(case.get("prior_tool_activity", False)),
        pending_question=bool(case.get("pending_question", False)),
        notebook_state=bool(case.get("notebook_state", False)),
    )


def run_harness() -> dict[str, Any]:
    routed = []
    for case in CASES:
        actual = _route(case)
        expected = case["expected"]
        # ``None`` is the deliberate hand-off to the semantic judge.
        routed.append({**case, "actual": actual, "pass": actual == expected})

    budget = TurnBudget(max_units=5, max_tool_calls=3, max_mutations=1)
    budget_checks = [
        budget.try_consume_tool(cost=1, mutating=False)[0],
        budget.try_consume_tool(cost=1, mutating=False)[0],
        budget.try_consume_tool(cost=3, mutating=True)[0],
        not budget.try_consume_tool(cost=4, mutating=True)[0],
    ]

    contracts: dict[str, Any] = {}
    for lens, names in REQUIRED_TOOLS.items():
        contracts[lens] = {
            name: {
                "present": bool(TOOL_REGISTRY.get(name, lens=lens)),
                "budget": getattr(TOOL_REGISTRY.get(name, lens=lens), "effective_budget", None),
            }
            for name in names
        }

    passed_routes = sum(1 for item in routed if item["pass"])
    total_routes = len(routed)
    return {
        "harness": HARNESS_VERSION,
        "routing": {
            "cases": routed,
            "passed": passed_routes,
            "total": total_routes,
            "accuracy": round(passed_routes / total_routes, 4) if total_routes else 1.0,
            "judge_handoff_cases": sum(item["actual"] is None for item in routed),
        },
        "tool_contracts": contracts,
        "budget": {
            "checks_passed": all(budget_checks),
            "checks": budget_checks,
            "snapshot": budget.snapshot(),
        },
        "bounded_json_valid": json.loads(bounded_json({"large": "x" * 5000, "status": "ok"}, 120, priority_keys=("status",)))["status"] == "ok",
    }


def main() -> None:
    print(json.dumps(run_harness(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
