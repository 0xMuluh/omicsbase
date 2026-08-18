"""Shared contracts and bounded accounting for OmicsBase agent runtimes.

The OpenHands adapter owns the conversation loop. This module intentionally
contains only runtime-neutral values shared by domain executors and the
adapter: budgets, tool-call results, user-facing labels, and bounded audit
arguments."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

MAX_PERSISTED_TOOL_ARGUMENT_CHARS = 4000


@dataclass
class TurnBudget:
    """Bounded per-run resources shared by all OmicsBase agent lenses."""

    max_units: int | None
    max_tool_calls: int | None
    max_mutations: int | None
    max_llm_calls: int | None = 8
    max_generated_tokens: int | None = 20000
    max_retrieved_chars: int | None = 80000
    max_input_tokens: int | None = 80000
    max_total_tokens: int | None = 100000
    units_used: int = 0
    tool_calls: int = 0
    mutation_count: int = 0
    llm_calls: int = 0
    generated_tokens: int = 0
    retrieved_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    @classmethod
    def from_settings(cls, *, profile: str = "agent") -> "TurnBudget":
        """Build one bounded budget for the selected agent run."""
        prefixes = {
            "note": "note_agent",
            "project": "project_agent",
        }
        prefix = prefixes.get(profile, "agent")

        def optional_limit(name: str, default: int) -> int | None:
            """Resolve an administrator limit; zero or less means unbounded.

            Project orchestration is goal-driven, so deployments may choose to
            let provider/account limits govern a run instead of imposing an
            OmicsBase turn ceiling. Other agent profiles retain their positive
            defaults.
            """
            raw = int(getattr(settings, f"{prefix}_{name}", default))
            return raw if raw > 0 else None

        return cls(
            max_units=optional_limit("max_budget_units", 12),
            max_tool_calls=optional_limit("max_tool_calls", 24),
            max_mutations=optional_limit("max_mutations", 4),
            max_llm_calls=optional_limit("max_llm_calls", 8),
            max_generated_tokens=optional_limit("max_generated_tokens", 20000),
            max_retrieved_chars=optional_limit("max_retrieved_chars", 80000),
            max_input_tokens=optional_limit("max_input_tokens", 80000),
            max_total_tokens=optional_limit("max_total_tokens", 100000),
        )

    def try_consume_tool(self, *, cost: int, mutating: bool) -> tuple[bool, str | None]:
        cost = max(1, int(cost))
        if self.max_tool_calls is not None and self.tool_calls >= self.max_tool_calls:
            return False, f"the run allows at most {self.max_tool_calls} tool calls"
        if mutating and self.max_mutations is not None and self.mutation_count >= self.max_mutations:
            return False, f"the run allows at most {self.max_mutations} mutations"
        if self.max_units is not None and self.units_used + cost > self.max_units:
            return False, f"the run has {self.max_units - self.units_used} budget units left, but this tool costs {cost}"
        self.tool_calls += 1
        self.units_used += cost
        if mutating:
            self.mutation_count += 1
        return True, None

    def try_record_llm_call(self) -> tuple[bool, str | None]:
        if self.max_llm_calls is not None and self.llm_calls >= self.max_llm_calls:
            return False, f"the run allows at most {self.max_llm_calls} LLM calls"
        self.llm_calls += 1
        return True, None

    def record_llm_call(self) -> None:
        """Compatibility helper for callers that do not need enforcement."""
        self.llm_calls += 1

    def record_generated(self, value: str) -> bool:
        amount = max(0, (len(value or "") + 3) // 4)
        if self.max_generated_tokens is not None and self.generated_tokens + amount > self.max_generated_tokens:
            return False
        self.generated_tokens += amount
        return True

    def record_usage(self, usage: dict[str, Any]) -> tuple[bool, str | None]:
        """Account provider usage and stop before an unbounded agent run."""
        try:
            input_tokens = max(0, int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0))
        except (TypeError, ValueError):
            input_tokens = 0
        try:
            output_tokens = max(0, int(usage.get("output_tokens") or usage.get("completion_tokens") or 0))
        except (TypeError, ValueError):
            output_tokens = 0
        try:
            total_tokens = max(0, int(usage.get("total_tokens") or input_tokens + output_tokens))
        except (TypeError, ValueError):
            total_tokens = input_tokens + output_tokens

        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens
        if self.max_input_tokens is not None and self.input_tokens > self.max_input_tokens:
            return False, f"the run allows at most {self.max_input_tokens} input tokens"
        if self.max_total_tokens is not None and self.total_tokens > self.max_total_tokens:
            return False, f"the run allows at most {self.max_total_tokens} total tokens"
        return True, None

    def record_retrieved(self, value: str) -> bool:
        amount = len(value or "")
        if self.max_retrieved_chars is not None and self.retrieved_chars + amount > self.max_retrieved_chars:
            return False
        self.retrieved_chars += amount
        return True

    def snapshot(self) -> dict[str, int | None]:
        return {
            "units_used": self.units_used,
            "max_units": self.max_units,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "mutation_count": self.mutation_count,
            "max_mutations": self.max_mutations,
            "max_llm_calls": self.max_llm_calls,
            "max_generated_tokens": self.max_generated_tokens,
            "max_retrieved_chars": self.max_retrieved_chars,
            "max_input_tokens": self.max_input_tokens,
            "max_total_tokens": self.max_total_tokens,
            "llm_calls": self.llm_calls,
            "generated_tokens": self.generated_tokens,
            "retrieved_chars": self.retrieved_chars,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

# User-facing labels for internal tool names. Raw identifiers must never
# reach the UI ("Using run_r_cell"); each lens maps its tools here.
_FRIENDLY_TOOL_LABELS = {
    # Note lens
    "inspect_note": "Reviewing the notebook",
    "run_r_cell": "Running R cell",
    "add_note": "Adding a note",
    "promote_to_workspace": "Promoting to workspace",
    "inspect_data_files": "Checking data files",
    # Workspace lens
    "inspect_project": "Inspecting the project",
    "list_recipes": "Listing analysis recipes",
    "list_importable_datasets": "Finding example datasets",
    "list_files": "Listing workspace files",
    "search_workspace": "Searching the workspace",
    "search_bioc_books": "Searching Bioconductor books",
    "recall_memory": "Recalling project memory",
    "read_file": "Reading file",
    "read_results": "Reading results",
    "compare_results": "Comparing results",
    "inspect_failures": "Checking failed jobs",
    "validate_report": "Validating report",
    "run_r": "Running R inspection",
    "run_r_script": "Running R script",
    "run_r_script": "Running R script",
    "ask_user": "Asking you a question",
    "import_package_data": "Importing example dataset",
    "fetch_url": "Fetching file from URL",
    "plan_analysis": "Planning the analysis",
    "set_recipe_enabled": "Updating recipe settings",
    "update_recipe_parameters": "Updating recipe parameters",
    "set_analysis_variables": "Updating analysis variables",
    "run_recipe": "Running recipe",
    "run_analysis": "Running the analysis",
    "render_report": "Rendering the report",
    "repair_report": "Repairing the report",
    "rollback_analysis_configuration": "Rolling back configuration",
    "edit_project": "Editing project files",
    "queue_guidance": "Queuing guidance",
}


def friendly_tool_label(tool_name: str) -> str:
    """Human-readable status text for a tool call, never the raw identifier."""
    known = _FRIENDLY_TOOL_LABELS.get(tool_name)
    if known:
        return known
    # Fallback: humanize any unmapped snake_case name.
    words = str(tool_name).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Working"


def persistable_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep tool-call audit metadata bounded without storing tool results."""
    try:
        encoded = json.dumps(arguments, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return {"_unserializable": str(arguments)[:MAX_PERSISTED_TOOL_ARGUMENT_CHARS]}
    if len(encoded) <= MAX_PERSISTED_TOOL_ARGUMENT_CHARS:
        return arguments
    return {
        "_truncated": True,
        "preview": encoded[:MAX_PERSISTED_TOOL_ARGUMENT_CHARS],
    }


def tool_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        return json.dumps({"tool": tool_name, "arguments": arguments}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"{tool_name}:{arguments!r}"


@dataclass
class ToolCallResult:
    """One tool call: the observation plus every event the lens emits for it.

    ``events`` are yielded in order before the generic trailing
    ``tool_completed``. ``end_turn`` ends the turn immediately (async
    actions, questions); ``final_event`` is yielded right before that.
    """

    observation: dict
    events: list[dict] = field(default_factory=list)
    summary: str | None = None
    end_turn: bool = False
    final_event: dict | None = None
    refresh_context: bool = False
    record_failure: bool = True
    emit_completed: bool = True
    wait_for: dict[str, Any] | None = None
