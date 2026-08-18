"""Declarative tool specifications shared by the OmicsBase agent lenses.

The model-facing JSON schema is only one part of a tool contract.  The
registry also records the execution boundary (lens, risk, capability,
idempotency, and whether a call may run in parallel).  Keeping those fields
next to the schema lets routing and tests reason about the same contract that
is advertised to the model.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


def _strict_schema(value: dict[str, Any]) -> dict[str, Any]:
    """Copy a JSON schema and make object boundaries closed by default.

    A nested free-form object can opt in with ``additionalProperties=True``.
    This prevents silent argument drift while preserving deliberately dynamic
    recipe/parameter payloads.
    """
    schema = copy.deepcopy(value)

    def visit(node: Any, *, root: bool = False) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        properties = node.get("properties")
        if isinstance(properties, dict):
            for child in properties.values():
                visit(child)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items)
        for key in ("oneOf", "anyOf", "allOf"):
            for child in node.get(key) or []:
                visit(child)

    visit(schema, root=True)
    return schema


@dataclass(frozen=True)
class ToolSpec:
    """One declarative model tool and its execution metadata."""

    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    lens: str = "workspace"
    kind: str = "inspect"  # inspect, inline, async
    risk: str = "read"  # read, write, execute, question
    state: str = "stable"
    capability: str | None = None
    intent: str | None = None
    parallel: bool = False
    idempotency: str = "read_only"  # read_only, idempotent, non_idempotent
    budget: int | None = None
    label: str | None = None
    advertised: bool = True
    alias_of: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"Invalid tool name: {self.name!r}")
        if self.kind not in {"inspect", "inline", "async"}:
            raise ValueError(f"Invalid tool kind for {self.name}: {self.kind}")
        if self.risk not in {"read", "write", "execute", "question"}:
            raise ValueError(f"Invalid tool risk for {self.name}: {self.risk}")
        if self.idempotency not in {"read_only", "idempotent", "non_idempotent"}:
            raise ValueError(f"Invalid idempotency for {self.name}: {self.idempotency}")

    @property
    def parameters(self) -> dict[str, Any]:
        return _strict_schema(self.schema)

    @property
    def effective_budget(self) -> int:
        """Return the minimum turn-budget cost for this tool."""
        if self.budget is not None:
            return max(1, int(self.budget))
        if self.risk == "execute":
            return 4
        if self.risk == "write":
            return 3
        return 1

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "lens": self.lens,
            "kind": self.kind,
            "risk": self.risk,
            "state": self.state,
            "capability": self.capability,
            "intent": self.intent,
            "parallel": self.parallel,
            "idempotency": self.idempotency,
            "budget": self.budget,
            "label": self.label or self.name,
            "advertised": self.advertised,
            "alias_of": self.alias_of,
            "schema": self.parameters,
        }


class ToolSpecRegistry:
    """Immutable-by-convention registry with capability-aware projections."""

    def __init__(self, specs: Iterable[ToolSpec]):
        ordered = tuple(specs)
        keys = [(spec.lens, spec.name) for spec in ordered]
        if len(keys) != len(set(keys)):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise ValueError(f"Duplicate tool specification(s): {duplicates}")
        self._specs = ordered
        self._by_key = {(spec.lens, spec.name): spec for spec in ordered}

    def get(self, name: str, *, lens: str | None = None) -> ToolSpec | None:
        wanted = str(name)
        if lens is not None:
            return self._by_key.get((lens, wanted))
        # Shared names (e.g. search_bioc_books) use the workspace contract as
        # the default; callers with another lens can pass it explicitly.
        return self._by_key.get(("workspace", wanted)) or self._by_key.get(("note", wanted))

    def require(self, name: str, *, lens: str | None = None) -> ToolSpec:
        spec = self.get(name, lens=lens)
        if spec is None:
            raise KeyError(name)
        return spec

    def all(self, *, lens: str | None = None) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in self._specs if lens is None or spec.lens == lens)

    def advertised(
        self,
        *,
        lens: str = "workspace",
        capabilities: set[str] | None = None,
        include_aliases: bool = False,
    ) -> tuple[ToolSpec, ...]:
        available = set(capabilities or ())
        result = []
        for spec in self.all(lens=lens):
            if not spec.advertised and not include_aliases:
                continue
            if spec.capability and spec.capability not in available:
                continue
            result.append(spec)
        return tuple(result)

    def openai_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [spec.as_openai() for spec in self.advertised(**kwargs)]

    def signature(self, name: str, arguments: dict[str, Any]) -> str:
        return json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, default=str)


def _schema(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


WORKSPACE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("inspect_project", "Get project status, study manifest, analysis plan, and recent actions"),
    ToolSpec("list_recipes", "List available analysis recipes for this project domain with parameters and enabled state", capability="legacy_recipe", parallel=True),
    ToolSpec("list_importable_datasets", "List R package datasets that can be imported into the study", capability="acquisition", parallel=True),
    ToolSpec("list_files", "List all files in the project workspace", parallel=True),
    ToolSpec("search_workspace", "Search workspace artifacts by text query", _schema({"query": {"type": "string", "description": "Search query"}, "limit": {"type": "integer", "default": 8}}, required=["query"]), parallel=True),
    ToolSpec("search_bioc_books", "Search the pinned stable Bioconductor books for methodological guidance, reusable QMD examples, and runnable worked examples. Prefer adapting a book worked example over inventing your own when explaining a concept.", _schema({"query": {"type": "string", "description": "Scientific or coding question"}, "channel": {"type": "string", "enum": ["stable", "preview"], "default": "stable"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}, "book": {"type": "string", "description": "Optional curated book slug"}}, required=["query"])),
    ToolSpec("recall_memory", "Recall durable project memories (preferences, decisions, constraints, findings)"),
    ToolSpec("read_file", "Read a bounded line range from a workspace file and return its content hash", _schema({"path": {"type": "string", "description": "Relative file path"}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}, "around": {"type": "integer", "minimum": 1, "description": "Read a small window around this line"}, "max_chars": {"type": "integer", "minimum": 256, "maximum": 16000, "default": 16000}}, required=["path"]), parallel=True),
    ToolSpec("read_results", "Read one explicitly named result artifact with bounded rows, columns, filters, and ordering", _schema({"path": {"type": "string", "description": "Project-relative path to the exact result artifact"}, "columns": {"type": "array", "items": {"type": "string"}}, "where": {"type": "object", "additionalProperties": True, "description": "Exact equality filters by column"}, "sort": {"type": "string"}, "sort_direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"}, "offset": {"type": "integer", "minimum": 0, "default": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}, "max_chars": {"type": "integer", "minimum": 256, "maximum": 16000}}, required=["path"]), parallel=True),
    ToolSpec("inspect_table", "Inspect a CSV or TSV schema without returning participant rows", _schema({"path": {"type": "string"}}, required=["path"]), parallel=True),
    ToolSpec("inspect_factor_levels", "Count levels for one CSV or TSV column", _schema({"path": {"type": "string"}, "column": {"type": "string"}}, required=["path", "column"]), parallel=True),
    ToolSpec("summarize_missingness", "Summarize missing values by CSV or TSV column", _schema({"path": {"type": "string"}}, required=["path"]), parallel=True),
    ToolSpec("check_sample_alignment", "Check feature-table sample columns against metadata sample identifiers", _schema({"feature_table": {"type": "string"}, "metadata": {"type": "string"}, "sample_id_column": {"type": "string"}}, required=["feature_table", "metadata", "sample_id_column"]), parallel=True),
    ToolSpec("check_design_matrix", "Build a deterministic categorical design matrix and report rank or aliasing", _schema({"metadata": {"type": "string"}, "terms": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 20}, "include_intercept": {"type": "boolean", "default": True}}, required=["metadata", "terms"]), parallel=True),
    ToolSpec("check_confounding", "Check deterministic pairwise confounding among metadata design terms", _schema({"metadata": {"type": "string"}, "terms": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 2, "maxItems": 8}}, required=["metadata", "terms"]), parallel=True),
    ToolSpec("compare_results", "Load multiple result artifacts for comparison", _schema({"paths": {"type": "array", "items": {"type": "string"}, "description": "List of result file paths"}}, required=["paths"]), parallel=True),
    ToolSpec("inspect_failures", "Inspect recent failed jobs with error details and logs"),
    ToolSpec("validate_report", "Validate the current rendered report for issues", parallel=True),
    ToolSpec("run_r", "Run a short R inspection only when a typed workspace inspection cannot answer the question", _schema({"code": {"type": "string", "maxLength": 4000, "description": "R code to execute; no network, package installation, writes, or shell commands"}, "purpose": {"type": "string", "description": "Brief description of what this inspection checks"}}, required=["code"]), idempotency="non_idempotent", parallel=False, risk="execute"),
    ToolSpec("run_r_script", "Execute an R script that exists in the workspace with the project directory as the working directory. Writes inside the workspace are allowed; package installation, shell, and network are not. Use this to run or re-run analysis code and observe its real output.", _schema({"path": {"type": "string", "description": "Project-relative path to the .R file"}, "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 1800, "default": 600}}, required=["path"]), kind="inline", risk="execute", idempotency="non_idempotent"),
    ToolSpec("ask_user", "Ask the user one blocking question with concrete options when a decision cannot be inferred", _schema({"question": {"type": "string", "description": "The question, phrased so the options answer it directly"}, "options": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "string", "minLength": 1}, "description": "2-6 concrete options"}, "multiple": {"type": "boolean", "description": "Allow multiple selections", "default": False}}, required=["question", "options"]), risk="question", idempotency="non_idempotent", parallel=False),
)


_EDIT_COMMON_PROPERTIES = {
    "reason": {"type": "string"},
    "approval": {"type": "string", "enum": ["auto", "preview", "require"], "default": "auto", "description": "Use preview/require to prepare a diff for explicit approval before committing."},
}

_EDIT_OPERATION_ITEM = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
                "allow_multiple": {"type": "boolean", "default": False},
                "reason": {"type": "string"},
            },
            "required": ["path", "search", "replace"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "*** Begin Patch envelope"},
                "reason": {"type": "string"},
            },
            "required": ["patch"],
            "additionalProperties": False,
        },
    ]
}


def _edit_branch(mode: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mode": {"const": mode},
            **_EDIT_COMMON_PROPERTIES,
            **properties,
        },
        "required": ["mode", *required],
        "additionalProperties": False,
    }


_EDIT_ENVELOPE_PROPERTIES = {
    "mode": {"type": "string", "enum": ["search_replace", "content", "patch", "batch"]},
    "path": {"type": "string"},
    "search": {"type": "string"},
    "replace": {"type": "string"},
    "content": {"type": "string"},
    "patch": {"type": "string", "description": "*** Begin Patch envelope"},
    "edits": {"type": "array", "items": _EDIT_OPERATION_ITEM},
    "allow_multiple": {"type": "boolean", "default": False},
    "reason": {"type": "string"},
    "expected_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}\x24"},
    "approval": {"type": "string", "enum": ["auto", "preview", "require"], "default": "auto"},
}


_EDIT_SCHEMA = {
    "type": "object",
    "properties": _EDIT_ENVELOPE_PROPERTIES,
    "additionalProperties": False,
    "oneOf": [
        _edit_branch(
            "search_replace",
            {
                "path": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
                "allow_multiple": {"type": "boolean", "default": False},
                "expected_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}\x24"},
            },
            ["path", "search", "replace"],
        ),
        _edit_branch(
            "content",
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}\x24"},
            },
            ["path", "content"],
        ),
        _edit_branch(
            "patch",
            {"patch": {"type": "string", "description": "*** Begin Patch envelope"}},
            ["patch"],
        ),
        _edit_branch(
            "batch",
            {"edits": {"type": "array", "items": _EDIT_OPERATION_ITEM}},
            ["edits"],
        ),
    ]
}


ACTION_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("import_package_data", "Import an R package dataset into the study", _schema({"package": {"type": "string"}, "dataset": {"type": "string"}, "role": {"type": "string", "default": "auto"}}, required=["package", "dataset"]), kind="inline", risk="write", capability="acquisition", idempotency="non_idempotent"),
    ToolSpec("fetch_url", "Fetch a file from a URL into the study", _schema({"url": {"type": "string"}, "filename": {"type": "string"}, "role": {"type": "string", "default": "auto"}}, required=["url"]), kind="inline", risk="write", capability="acquisition", idempotency="non_idempotent"),
    ToolSpec("stage_report_pack", "Copy the team template named in the analysis plan (report_pack_id) into the workspace; existing files are overwritten. Optional — call once at the start of a build when a template fits, then adapt the copied files. Returns the staged file list.", kind="inline", risk="write", capability="report_execution", idempotency="idempotent"),
    ToolSpec("set_plan", "Create or replace this project's structured analysis plan. Supply the complete plan object: project_name, domain, report_pack_id, study_type, question, grouping_variable, group_levels, covariates, and workflow steps (id, name, classification, parameters). Schema violations are returned as observations; fix the flagged fields and call again.", _schema({"plan": {"type": "object", "additionalProperties": True, "description": "The complete AnalysisPlan object"}}, required=["plan"]), kind="inline", risk="write", idempotency="idempotent"),
    ToolSpec("set_recipe_enabled", "Enable or disable a recipe", _schema({"recipe_id": {"type": "string"}, "enabled": {"type": "boolean"}}, required=["recipe_id", "enabled"]), kind="async", risk="write", capability="legacy_recipe", idempotency="idempotent"),
    ToolSpec("update_recipe_parameters", "Update parameters for a recipe", _schema({"recipe_id": {"type": "string"}, "parameters": {"type": "object", "additionalProperties": True}}, required=["recipe_id", "parameters"]), kind="async", risk="write", capability="legacy_recipe", idempotency="idempotent"),
    ToolSpec("set_analysis_variables", "Set grouping variable, levels, and covariates", _schema({"grouping_variable": {"type": "string"}, "group_levels": {"type": "array", "items": {"type": "string"}}, "covariates": {"type": "array", "items": {"type": "string"}}}), kind="async", risk="write", idempotency="idempotent"),
    ToolSpec("run_recipe", "Run a specific recipe", _schema({"recipe_id": {"type": "string"}}, required=["recipe_id"]), kind="async", risk="execute", capability="legacy_recipe", idempotency="non_idempotent"),
    ToolSpec("undo_project_edit", "Undo one named committed project edit if its current bytes still match the journal", _schema({"transaction_id": {"type": "string", "pattern": "^[a-f0-9]{16,64}$", "description": "Edit transaction id from the workspace history"}}, required=["transaction_id"]), kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("render_report", "Render the Quarto report inside this conversation and return the full structured result: status, per-page errors, and log tails. If the render fails, read the errors, repair the workspace source, and render again until it passes.", kind="inline", risk="execute", capability="report_execution", idempotency="non_idempotent"),
    ToolSpec("rollback_analysis_configuration", "Rollback analysis configuration to previous state", kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("edit_project", "Edit project source through one atomic, hash-checked transaction. Choose exactly one mode: search_replace, content, patch, or batch. Inspect first and include the mode discriminator.", _EDIT_SCHEMA, kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("queue_guidance", "Queue guidance for after the current running job finishes", _schema({"guidance": {"type": "string"}}, required=["guidance"]), kind="async", risk="write", idempotency="non_idempotent"),
)


NOTE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("inspect_note", "Inspect the current linear notebook, including prior cells, executions, artifacts, and workspace objects. Use this before relying on existing notebook state, and prefer stored results over rerunning work.", lens="note"),
    ToolSpec("search_bioc_books", "Search the pinned QMD-derived Bioconductor books for relevant explanations, assumptions, reusable R examples, and runnable worked examples. Prefer adapting a book worked example over inventing your own when explaining a concept.", _schema({"query": {"type": "string", "description": "The scientific or coding question to search for."}, "channel": {"type": "string", "enum": ["stable", "preview"], "default": "stable"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}, "book": {"type": "string", "description": "Optional curated book slug."}}, required=["query"]), lens="note"),
    ToolSpec("run_r_cell", "Persist and queue R only for a requested computation or an explicitly useful demonstration. A queued or running execution is not a result: wait for completion, inspect the actual status and output, and never claim success without successful execution.", _schema({"code": {"type": "string", "description": "The complete R cell to persist and execute."}, "purpose": {"type": "string", "description": "What scientific question this cell checks."}, "parameters": {"type": "object", "additionalProperties": True, "description": "Explicit parameters used by the cell."}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800}}, required=["code"]), lens="note", kind="async", risk="execute", idempotency="non_idempotent"),
    ToolSpec("add_note", "Persist markdown when documenting an important choice, assumption, interpretation, methodology, or explicitly requested explanation. It is optional and is not required for every computation.", _schema({"text": {"type": "string", "description": "Concise markdown content for the durable note."}}, required=["text"]), lens="note", kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("promote_to_workspace", "Copy a tested R cell into the project's code directory after immutable provenance checks.", _schema({"cell_id": {"type": "string"}, "revision_id": {"type": "string"}, "execution_id": {"type": "string"}, "path": {"type": "string"}, "strategy": {"type": "string", "enum": ["replace", "append", "create_only"], "default": "create_only"}, "base_sha256": {"type": "string", "description": "SHA-256 of the current target, required when appending to or replacing an existing file"}, "purpose": {"type": "string"}}, required=["cell_id", "revision_id", "execution_id", "path"]), lens="note", kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("inspect_data_files", "List data files attached to this notebook with format, columns, and the R path to read each. Use this before reading an attached file when its path or schema is not already established.", lens="note"),
)

TOOL_REGISTRY = ToolSpecRegistry(WORKSPACE_TOOL_SPECS + ACTION_TOOL_SPECS + NOTE_TOOL_SPECS)


def workspace_tools(*, include_capabilities: set[str] | None = None) -> list[dict[str, Any]]:
    return TOOL_REGISTRY.openai_tools(lens="workspace", capabilities=include_capabilities or set(), include_aliases=False)


def note_tools() -> list[dict[str, Any]]:
    """Note tools are kept in note_agent for its richer notebook prompt."""
    return []


__all__ = [
    "ToolSpec",
    "ToolSpecRegistry",
    "TOOL_REGISTRY",
    "WORKSPACE_TOOL_SPECS",
    "ACTION_TOOL_SPECS",
    "NOTE_TOOL_SPECS",
]
