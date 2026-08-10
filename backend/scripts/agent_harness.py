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

HARNESS_VERSION = "omicsbase-agent-harness-v3"

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

def _fixture_case(
    name: str,
    tool: str,
    message: str,
    arguments: dict[str, Any],
    observation: dict[str, Any],
    marker: str,
    *,
    wait: bool = False,
) -> dict[str, Any]:
    case = {
        "name": f"canonical_{name}",
        "message": message,
        "scripts": [
            [{"type": "tool_call", "id": f"fixture-{name}", "name": tool, "arguments": arguments}],
            [{"type": "text_delta", "content": "The observed result is ready for review."}],
        ],
        "expected_tools": [tool],
        "observations": {tool: observation},
        "expected_observation": marker,
    }
    if wait:
        case["wait_tool"] = tool
    return case


CANONICAL_EVAL_CASES: tuple[dict[str, Any], ...] = NATIVE_EVAL_CASES + tuple(
    _fixture_case(*item[:-1], wait=item[-1])
    for item in (
        ("project_status", "inspect_project", "What is the current project status?", {}, {"status": "ok", "project": {"status": "completed"}}, "completed", False),
        ("file_inventory", "list_files", "List the files in the workspace", {}, {"status": "ok", "files": ["code/main.R", "code/index.qmd"]}, "main.R", False),
        ("workspace_search", "search_workspace", "Find references to the grouping variable", {"query": "grouping variable"}, {"status": "ok", "matches": [{"path": "code/main.R", "line": 12}]}, "main.R", False),
        ("source_read", "read_file", "Read the analysis entry point", {"path": "code/main.R"}, {"status": "ok", "path": "code/main.R", "revision": "sha-1"}, "code/main.R", False),
        ("results_read", "read_results", "Read the primary result table", {"path": "output/results/primary.csv"}, {"status": "ok", "path": "output/results/primary.csv", "row_count": 42}, "row_count", False),
        ("factor_levels", "inspect_factor_levels", "Inspect the levels of the treatment column", {"path": "metadata.csv", "column": "treatment"}, {"status": "ok", "levels": [{"level": "control", "count": 12}]}, "control", False),
        ("missingness", "summarize_missingness", "Summarize missing values in the metadata", {"path": "metadata.csv"}, {"status": "ok", "missing": {"age": {"count": 2}}}, "missing", False),
        ("memory_recall", "recall_memory", "What study decisions have already been recorded?", {"query": "study decisions"}, {"status": "ok", "memory": {"decisions": ["Use age as a covariate"]}}, "covariate", False),
        ("table_schema", "inspect_table", "Inspect the uploaded table schema", {"path": "metadata.csv"}, {"status": "ok", "schema": {"columns": ["sample", "group"]}}, "columns", False),
        ("method_books", "search_bioc_books", "Find the pinned guidance on normalization", {"query": "normalization", "channel": "stable"}, {"status": "ok", "matches": [{"citation": "OSCA:normalization"}]}, "OSCA", False),
        ("alignment_duplicate", "check_sample_alignment", "Check sample alignment and duplicate identifiers", {"feature_table": "features.csv", "metadata": "metadata.csv", "sample_id_column": "sample"}, {"status": "ok", "aligned": False, "duplicate_metadata_ids": ["S1"]}, "duplicate", False),
        ("design_rank", "check_design_matrix", "Check rank after adding age and sex", {"metadata": "metadata.csv", "terms": ["age", "sex"]}, {"status": "ok", "rank": 3, "columns": 3, "full_rank": True}, "full_rank", False),
        ("confounding_site", "check_confounding", "Check treatment and site confounding", {"metadata": "metadata.csv", "terms": ["treatment", "site"]}, {"status": "ok", "confounded_pairs": [{"terms": ["treatment", "site"], "strongly_confounding": True}]}, "strongly_confounding", False),
        ("result_compare", "compare_results", "Compare primary and sensitivity results", {"paths": ["output/results/primary.csv", "output/results/sensitivity.csv"]}, {"status": "ok", "artifacts": [{"path": "primary.csv"}, {"path": "sensitivity.csv"}]}, "sensitivity", False),
        ("report_validation", "validate_report", "Validate the rendered report", {}, {"status": "ok", "summary": "No blocking presentation findings"}, "blocking", False),
        ("failed_dependency", "inspect_failures", "Why did the report fail to render?", {}, {"status": "ok", "failures": [{"failure_class": "dependency", "error": "Missing package"}]}, "dependency", False),
        ("failed_timeout", "inspect_failures", "Why did the analysis time out?", {}, {"status": "ok", "failures": [{"failure_class": "timeout", "error": "Job timed out"}]}, "timeout", False),
        ("missing_result", "read_results", "Explain why the requested result is missing", {"path": "output/results/missing.csv"}, {"status": "error", "error": "Artifact was not found", "available_artifacts": ["output/results/primary.csv"]}, "available", False),
        ("r_dependency", "run_r", "Diagnose the missing R package error", {"code": "library(missingPackage)"}, {"status": "error", "failure_class": "dependency", "error": "Package is not installed"}, "dependency", False),
        ("r_timeout", "run_r", "Explain the timed-out R inspection", {"code": "Sys.sleep(100)"}, {"status": "error", "failure_class": "timeout", "error": "Inspection timed out"}, "timeout", False),
        ("stale_edit", "edit_project", "Apply this edit only against the inspected revision", {"mode": "search_replace", "path": "code/main.R", "search": "old", "replace": "new", "expected_sha256": "stale"}, {"status": "error", "code": "edit_conflict", "error": "The file changed since inspection"}, "edit_conflict", False),
        ("batch_edit", "edit_project", "Apply these two hash-checked edits atomically", {"mode": "batch", "edits": [{"path": "code/a.R", "search": "a", "replace": "b"}, {"path": "code/b.R", "search": "c", "replace": "d"}]}, {"status": "ok", "transaction_id": "txn-1", "paths": ["code/a.R", "code/b.R"]}, "transaction_id", False),
        ("edit_review", "edit_project", "Show the edit for approval before writing", {"mode": "search_replace", "path": "code/main.R", "search": "old", "replace": "new", "approval": "preview"}, {"status": "review_required", "review_id": "review-1"}, "review_required", False),
        ("undo_conflict", "undo_project_edit", "Undo the last edit if the revision still matches", {"transaction_id": "deadbeefdeadbeef"}, {"status": "error", "code": "edit_conflict", "error": "Current bytes do not match the journal"}, "edit_conflict", False),
        ("user_guidance", "queue_guidance", "Queue this request until the active job finishes", {"guidance": "Render the report after the job"}, {"status": "ok", "queued": True, "guidance": "Render the report after the job"}, "queued", False),
        ("plan_pipeline", "plan_analysis", "Plan the analysis from the uploaded study", {}, {"status": "queued", "job_type": "plan"}, "plan", False),
        ("set_variables", "set_analysis_variables", "Use treatment as the grouping variable", {"grouping_variable": "treatment", "group_levels": ["control", "case"], "covariates": ["age"]}, {"status": "queued", "grouping_variable": "treatment"}, "treatment", False),
        ("render_report", "render_report", "Render and validate the report", {}, {"status": "queued", "job_type": "render"}, "render", False),
        ("run_analysis", "run_analysis", "Run the approved analysis", {"resume_from_checkpoint": True}, {"status": "queued", "job_type": "analysis"}, "analysis", False),
        ("rollback", "rollback_analysis_configuration", "Rollback the last analysis configuration", {}, {"status": "queued", "job_type": "rollback"}, "rollback", False),
        ("import_dataset", "import_package_data", "Import the supported example dataset", {"package": "phyloseq", "dataset": "GlobalPatterns"}, {"status": "ok", "files": ["GlobalPatterns_feature_table.csv"]}, "GlobalPatterns", False),
        ("list_skills", "list_skills", "List relevant scientific skill packs", {"limit": 5}, {"status": "ok", "skills": [{"id": "microbiome-analysis"}]}, "microbiome", False),
        ("load_skill", "load_skill", "Load the selected skill guidance", {"skill": "microbiome-analysis", "references": ["references/methods.md"]}, {"status": "ok", "skill": "microbiome-analysis", "content": "Use compositional methods"}, "compositional", False),
        ("fetch_url", "fetch_url", "Fetch the approved study file", {"url": "https://example.invalid/study.csv"}, {"status": "ok", "filename": "study.csv", "role": "metadata"}, "study.csv", False),
        ("ask_design", "ask_user", "Ask which column defines the biological groups", {"question": "Which column defines the groups?", "options": ["group", "condition"]}, {"status": "ok", "question": "Which column defines the groups?"}, "groups", False),
        ("async_note", "run_r_cell", "Run the cell and explain the result when complete", {"code": "mean(c(1, 2, 3))"}, {"status": "ok", "execution": {"id": "execution-2", "status": "queued"}}, "execution-2", True),
        ("async_report", "run_analysis", "Run the analysis, compare methods, then write sensitivity notes", {"resume_from_checkpoint": True}, {"status": "ok", "execution": {"id": "job-2", "status": "queued"}}, "job-2", True),
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
        expected_tool = str((case.get("expected_tools") or [""])[0])
        self.lens = str(case.get("lens") or ("note" if expected_tool in {"inspect_note", "run_r_cell", "add_note", "promote_to_workspace", "inspect_data_files"} else "workspace"))
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
        return TOOL_REGISTRY.get(tool_name, lens=self.lens) or TOOL_REGISTRY.get(tool_name)

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
    observation_retained = observation_forwarded or bool(case.get("wait_tool"))
    success = bool(final_events) and executor.tool_calls == expected_tools and observation_retained
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
        "observation_retained": observation_retained,
    }


def run_native_eval() -> dict[str, Any]:
    cases = [_run_native_eval_case(case) for case in CANONICAL_EVAL_CASES]
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
        "observations_retained": sum(1 for case in cases if case["observation_retained"]),
        "continuation_goal_retained": goal_retained,
    }


async def _run_live_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one fixture-backed case through the real provider stream."""
    from app.services.note_agent import NOTE_AGENT_SYSTEM_PROMPT
    from app.services.workspace_agent import AGENT_SYSTEM_PROMPT

    executor = _NativeEvalExecutor(case)
    executor.system_prompt = NOTE_AGENT_SYSTEM_PROMPT if executor.lens == "note" else AGENT_SYSTEM_PROMPT
    executor.tools = TOOL_REGISTRY.openai_tools(
        lens=executor.lens,
        capabilities={"acquisition", "report_execution", "legacy_recipe"} if executor.lens == "workspace" else set(),
    )
    original_stream = agent_core.stream_llm_with_tools

    async def counting_stream(**kwargs):
        executor.provider_rounds += 1
        executor.provider_messages.append(kwargs.get("messages") or [])
        async for event in original_stream(**kwargs):
            yield event

    agent_core.stream_llm_with_tools = counting_stream
    try:
        events = [event async for event in agent_core.run_agent_loop(executor, case["message"])]
    except Exception as exc:
        return {
            "name": case["name"],
            "success": False,
            "error": str(exc)[:2000],
            "expected_tools": list(case.get("expected_tools") or []),
            "actual_tools": executor.tool_calls,
            "llm_calls": executor.provider_rounds,
        }
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
    observation_retained = observation_forwarded or bool(case.get("wait_tool"))
    success = bool(final_events) and executor.tool_calls == expected_tools and observation_retained
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
        "observation_retained": observation_retained,
    }


async def run_live_eval() -> dict[str, Any]:
    """Run all canonical cases against the configured provider.

    Tools are fixture-backed and never touch the database, filesystem, or
    network. Only the configured LLM provider is contacted, and this function
    is intentionally opt-in through the ``--live`` CLI flag.
    """
    from app.config import settings

    cases = []
    for case in CANONICAL_EVAL_CASES:
        cases.append(await _run_live_eval_case(case))
    passed = sum(1 for case in cases if case.get("success"))
    return {
        "harness": HARNESS_VERSION,
        "provider_backed": True,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "fixture_tools": True,
        "cases": cases,
        "passed": passed,
        "total": len(cases),
        "success_rate": round(passed / len(cases), 4) if cases else 1.0,
        "tool_calls": sum(len(case.get("actual_tools") or []) for case in cases),
        "unnecessary_tool_calls": sum(case.get("unnecessary_tool_calls", 0) for case in cases),
        "llm_calls": sum(case.get("llm_calls", 0) for case in cases),
        "wait_transitions": sum(case.get("wait_transitions", 0) for case in cases),
        "observations_forwarded": sum(1 for case in cases if case.get("observation_forwarded")),
        "observations_retained": sum(1 for case in cases if case.get("observation_retained")),
    }


def main() -> None:
    if "--live" in sys.argv:
        print(json.dumps(asyncio.run(run_live_eval()), indent=2, sort_keys=True))
        return
    print(json.dumps(run_harness(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
