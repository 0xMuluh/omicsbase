"""Deterministic routing and native-agent evaluation harness.

This harness intentionally makes no provider, network, or database calls. It
measures the safety-critical decisions that should remain stable even when an
LLM is unavailable: request routing, tool-contract coverage, and budget
accounting.
"""

from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import agent_core
from app.services.agent_core import ToolCallResult, TurnBudget
from app.services.agent_plans import build_continuation_plan, continuation_prompt
from app.services.context_budget import bounded_json
from app.services.intent_fastpath import deterministic_intent
from app.services.tool_specs import TOOL_REGISTRY

HARNESS_VERSION = "omicsbase-agent-harness-v2"

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
        "native_eval": run_native_eval(),
        "bounded_json_valid": json.loads(bounded_json({"large": "x" * 5000, "status": "ok"}, 120, priority_keys=("status",)))["status"] == "ok",
    }


NATIVE_EVAL_CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "native_conceptual_answer",
        "message": "What is a p-value?",
        "scripts": [[{"type": "text_delta", "content": "A p-value measures evidence against a null model."}],],
        "expected_tools": [],
    },
    {
        "name": "native_schema_inspection",
        "message": "Inspect the metadata schema",
        "scripts": [
            [{"type": "tool_call", "id": "schema-1", "name": "inspect_table", "arguments": {"path": "metadata.csv"}}],
            [{"type": "text_delta", "content": "The metadata schema is available above."}],
        ],
        "expected_tools": ["inspect_table"],
        "observations": {"inspect_table": {"status": "ok", "columns": ["sample", "group"]}},
    },
    {
        "name": "native_failure_diagnosis",
        "message": "Why did the report fail?",
        "scripts": [
            [{"type": "tool_call", "id": "failure-1", "name": "inspect_failures", "arguments": {}}],
            [{"type": "text_delta", "content": "The failure needs review before another run."}],
        ],
        "expected_tools": ["inspect_failures"],
        "observations": {"inspect_failures": {"status": "ok", "failures": [{"class": "dependency"}]}},
    },
    {
        "name": "native_knowledge_retrieval",
        "message": "Compare methods using the pinned books",
        "scripts": [
            [{"type": "tool_call", "id": "book-1", "name": "search_bioc_books", "arguments": {"query": "normalization"}}],
            [{"type": "text_delta", "content": "The pinned guidance compares the methods."}],
        ],
        "expected_tools": ["search_bioc_books"],
        "observations": {"search_bioc_books": {"status": "ok", "matches": [{"citation": "book:section"}]}},
    },
    {
        "name": "native_async_wait",
        "message": "Run the notebook cell and explain it when complete",
        "scripts": [[{"type": "tool_call", "id": "cell-1", "name": "run_r_cell", "arguments": {"code": "1 + 1"}}]],
        "expected_tools": ["run_r_cell"],
        "wait_tool": "run_r_cell",
        "observations": {"run_r_cell": {"status": "ok", "execution": {"id": "execution-1", "status": "queued"}}},
    },
)

# Additional canonical cases exercise typed design diagnostics, failure paths,
# edit conflicts, and clarification rather than only happy-path routing.
NATIVE_EVAL_CASES = NATIVE_EVAL_CASES + tuple(
    {
        "name": f"native_{name}",
        "message": message,
        "scripts": [
            [{"type": "tool_call", "id": f"diagnostic-{name}", "name": tool, "arguments": arguments}],
            [{"type": "text_delta", "content": "The observed result is ready for review."}],
        ],
        "expected_tools": [tool],
        "observations": {tool: observation},
        "expected_observation": marker,
    }
    for name, tool, message, arguments, observation, marker in (
        (
            "sample_alignment",
            "check_sample_alignment",
            "Check that the feature table and metadata samples align",
            {"feature_table": "features.csv", "metadata": "metadata.csv", "sample_id_column": "sample"},
            {"status": "ok", "aligned": True, "aligned_count": 24},
            "aligned_count",
        ),
        (
            "design_matrix",
            "check_design_matrix",
            "Check whether the design matrix is estimable",
            {"metadata": "metadata.csv", "terms": ["group", "site"]},
            {"status": "ok", "full_rank": False, "aliased_terms": ["site"]},
            "aliased_terms",
        ),
        (
            "confounding",
            "check_confounding",
            "Check for confounding between group and site",
            {"metadata": "metadata.csv", "terms": ["group", "site"]},
            {"status": "ok", "confounded_pairs": [{"strongly_confounding": True}]},
            "strongly_confounding",
        ),
        (
            "stale_edit_conflict",
            "edit_project",
            "Apply the edit only if the file revision is still current",
            {"mode": "search_replace", "path": "code/main.R", "search": "old", "replace": "new", "expected_sha256": "stale"},
            {"status": "error", "code": "edit_conflict", "error": "The file changed since inspection."},
            "edit_conflict",
        ),
        (
            "missing_package",
            "run_r",
            "Why did the R inspection fail?",
            {"code": "library(missingPackage)"},
            {"status": "error", "failure_class": "dependency", "error": "Package is not installed."},
            "dependency",
        ),
        (
            "timeout_failure",
            "inspect_failures",
            "Explain the timed-out analysis",
            {},
            {"status": "ok", "failures": [{"failure_class": "timeout", "error": "Job timed out."}]},
            "timeout",
        ),
        (
            "result_comparison",
            "compare_results",
            "Compare the two result artifacts",
            {"paths": ["output/results/a.csv", "output/results/b.csv"]},
            {"status": "ok", "artifacts": [{"path": "a.csv"}, {"path": "b.csv"}]},
            "artifacts",
        ),
        (
            "clarification",
            "ask_user",
            "Ask which grouping variable the study uses",
            {"question": "Which column defines the groups?", "options": ["group", "condition"]},
            {"status": "ok", "question": "Which column defines the groups?"},
            "question",
        ),
    )
)


class _NativeEvalExecutor:
    max_steps = 4
    max_tokens = 400
    max_tool_chars = 2000
    system_prompt = "native eval"
    tools: list[dict[str, Any]] = []
    use_retry_guard = False
    llm_provider_override = None
    llm_model_override = None
    cancelled_message = "cancelled"
    default_final_message = "No final answer"
    max_steps_message = "max steps"

    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.tool_calls: list[str] = []
        self.provider_rounds = 0
        self.provider_messages: list[list[dict[str, Any]]] = []
        self.observation_chars = 0

    def initial_events(self, message):
        return [], False

    def build_messages(self, message):
        return [{"role": "user", "content": message}]

    def build_live_context(self):
        return "fixture context"

    def fallback_events(self, exc):
        return [{"type": "final", "message": str(exc)}]

    def final_event(self, message):
        return {"type": "final", "message": message}

    def max_steps_events(self):
        return [{"type": "final", "message": self.max_steps_message}]

    def tool_completed_event(self, tool_name, tool_call_id, arguments, status, summary, step):
        return {"type": "tool_completed", "tool": tool_name, "status": status}

    def summary_for(self, tool_name, observation):
        return str(observation.get("status") or "ok")

    def use_fast_path(self, message):
        return False

    def parallel_eligible(self, tool_name):
        return False

    def tool_spec(self, tool_name):
        return TOOL_REGISTRY.get(tool_name, lens="workspace")

    async def legacy_llm_step(self, messages, *, step):
        return None

    async def execute_tool(self, tool_name, arguments, **kwargs):
        self.tool_calls.append(tool_name)
        observation = dict((self.case.get("observations") or {}).get(tool_name) or {"status": "ok"})
        self.observation_chars += len(json.dumps(observation, sort_keys=True, default=str))
        if tool_name == self.case.get("wait_tool"):
            execution = observation.get("execution") or {}
            return ToolCallResult(
                observation=observation,
                wait_for={"kind": "execution", "id": str(execution.get("id") or "unknown")},
                final_event={"type": "final", "message": "Waiting for the dependency."},
            )
        return ToolCallResult(observation=observation)


def _run_native_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    executor = _NativeEvalExecutor(case)

    async def fake_stream(**kwargs):
        index = executor.provider_rounds
        executor.provider_rounds += 1
        executor.provider_messages.append(kwargs.get("messages") or [])
        for event in case["scripts"][index]:
            yield event
        yield {"type": "done"}

    async def collect():
        return [event async for event in agent_core.run_agent_loop(executor, case["message"])]

    original_stream = agent_core.stream_llm_with_tools
    agent_core.stream_llm_with_tools = fake_stream
    try:
        events = asyncio.run(collect())
    finally:
        agent_core.stream_llm_with_tools = original_stream
    expected_tools = list(case.get("expected_tools") or [])
    wait_events = [event for event in events if event.get("type") == "wait"]
    final_events = [event for event in events if event.get("type") == "final"]
    expected_marker = str(case.get("expected_observation") or "")
    observation_forwarded = (
        not expected_marker
        or expected_marker in json.dumps(executor.provider_messages[1:], default=str, sort_keys=True)
    )
    success = bool(final_events) and executor.tool_calls == expected_tools and observation_forwarded
    if case.get("wait_tool"):

        success = success and len(wait_events) == 1
    return {
        "name": case["name"],
        "success": success,
        "expected_tools": expected_tools,
        "actual_tools": executor.tool_calls,
        "unnecessary_tool_calls": max(0, len(executor.tool_calls) - len(expected_tools)),
        "llm_calls": executor.provider_rounds,
        "retrieved_chars": executor.observation_chars,
        "wait_transitions": len(wait_events),
        "final": bool(final_events),
        "observation_forwarded": observation_forwarded,
    }


def run_native_eval() -> dict[str, Any]:
    cases = [_run_native_eval_case(case) for case in NATIVE_EVAL_CASES]
    run = type("ContinuationRun", (), {
        "id": "eval-run",
        "surface": "workspace",
        "project_id": "project",
        "note_thread_id": None,
        "run_metadata": {},
        "resumable": False,
    })()
    plan = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-eval",
        instruction="Run the analysis, compare the methods, and add a sensitivity section if they disagree.",
        arguments={"resume_from_checkpoint": True},
        dependency_status="completed",
    )
    prompt = continuation_prompt(plan)
    goal_retained = "compare the methods" in prompt and "resume_from_checkpoint" in prompt
    passed = sum(1 for case in cases if case["success"])
    return {
        "production_loop": "app.services.agent_core.run_agent_loop",
        "provider_independent": True,
        "database_free": True,
        "network_free": True,
        "cases": cases,
        "passed": passed,
        "total": len(cases),
        "success_rate": round(passed / len(cases), 4) if cases else 1.0,
        "tool_calls": sum(len(case["actual_tools"]) for case in cases),
        "unnecessary_tool_calls": sum(case["unnecessary_tool_calls"] for case in cases),
        "llm_calls": sum(case["llm_calls"] for case in cases),
        "wait_transitions": sum(case["wait_transitions"] for case in cases),
        "observations_forwarded": sum(1 for case in cases if case["observation_forwarded"]),
        "continuation_goal_retained": goal_retained,
    }


def main() -> None:
    print(json.dumps(run_harness(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
