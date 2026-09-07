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
    ToolSpec(
        "view_file",
        "View the contents of a file in the workspace with line numbers and optional line range slicing.",
        _schema(
            {
                "path": {"type": "string", "description": "Relative path of the file to view."},
                "start_line": {"type": "integer", "description": "1-indexed starting line number (optional)."},
                "end_line": {"type": "integer", "description": "1-indexed ending line number (optional)."},
            },
            required=["path"],
        ),
        lens="workspace",
        kind="inspect",
        risk="read",
    ),
    ToolSpec(
        "replace_file_content",
        "Replace an exact contiguous block of text in a file. target_content must match existing text exactly.",
        _schema(
            {
                "path": {"type": "string", "description": "Relative path of the file to modify."},
                "target_content": {"type": "string", "description": "The exact character-sequence to be replaced."},
                "replacement_content": {"type": "string", "description": "The replacement content."},
                "allow_multiple": {"type": "boolean", "default": False, "description": "If true, replaces all occurrences."},
            },
            required=["path", "target_content", "replacement_content"],
        ),
        lens="workspace",
        kind="inline",
        risk="write",
        idempotency="non_idempotent",
    ),
    ToolSpec(
        "write_to_file",
        "Create a new file or completely overwrite an existing file with the provided content.",
        _schema(
            {
                "path": {"type": "string", "description": "Relative path of the file to create or overwrite."},
                "content": {"type": "string", "description": "The full content to write to the file."},
                "overwrite": {"type": "boolean", "default": True, "description": "Whether to overwrite if file exists."},
            },
            required=["path", "content"],
        ),
        lens="workspace",
        kind="inline",
        risk="write",
        idempotency="non_idempotent",
    ),
    ToolSpec(
        "list_dir",
        "List files and directories in the workspace project root or a subfolder.",
        _schema(
            {
                "path": {"type": "string", "description": "Relative directory path (defaults to root '.')."},
                "recursive": {"type": "boolean", "default": False, "description": "Whether to list recursively."},
            },
        ),
        lens="workspace",
        kind="inspect",
        risk="read",
    ),
    ToolSpec(
        "grep_search",
        "Fast text or regular expression search across workspace files.",
        _schema(
            {
                "query": {"type": "string", "description": "Search pattern or string to look for."},
                "path": {"type": "string", "description": "Subdirectory or file to search within."},
                "is_regex": {"type": "boolean", "default": False, "description": "Whether query is a regular expression."},
                "case_insensitive": {"type": "boolean", "default": True, "description": "Case-insensitive search."},
            },
            required=["query"],
        ),
        lens="workspace",
        kind="inspect",
        risk="read",
    ),
    ToolSpec(
        "run_command",
        "Execute a shell command (e.g. Rscript, quarto render, bash scripts) synchronously inside the project directory.",
        _schema(
            {
                "command": {"type": "string", "description": "Shell command string to execute."},
                "timeout_seconds": {"type": "integer", "default": 180, "description": "Execution timeout in seconds."},
            },
            required=["command"],
        ),
        lens="workspace",
        kind="async",
        risk="execute",
        idempotency="non_idempotent",
    ),
)


ACTION_TOOL_SPECS: tuple[ToolSpec, ...] = ()


NOTE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("inspect_note", "Inspect the current linear notebook, including prior cells, executions, artifacts, and workspace objects. Use this before relying on existing notebook state, and prefer stored results over rerunning work.", lens="note"),
    ToolSpec("search_bioc_books", "Search the pinned QMD-derived Bioconductor books for relevant explanations, assumptions, reusable R examples, and runnable worked examples. Prefer adapting a book worked example over inventing your own when explaining a concept.", _schema({"query": {"type": "string", "description": "The scientific or coding question to search for."}, "channel": {"type": "string", "enum": ["stable", "preview"], "default": "stable"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}, "book": {"type": "string", "description": "Optional curated book slug."}}, required=["query"]), lens="note"),
    ToolSpec("run_r_cell", "Persist and queue R only for a requested computation or an explicitly useful demonstration. A queued or running execution is not a result: wait for completion, inspect the actual status and output, and never claim success without successful execution.", _schema({"code": {"type": "string", "description": "The complete R cell to persist and execute."}, "purpose": {"type": "string", "description": "What scientific question this cell checks."}, "parameters": {"type": "object", "additionalProperties": True, "description": "Explicit parameters used by the cell."}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800}}, required=["code"]), lens="note", kind="async", risk="execute", idempotency="non_idempotent"),
    ToolSpec("add_note", "Persist markdown when documenting an important choice, assumption, interpretation, methodology, or explicitly requested explanation. It is optional and is not required for every computation.", _schema({"text": {"type": "string", "description": "Concise markdown content for the durable note."}}, required=["text"]), lens="note", kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("promote_to_workspace", "Copy a tested R cell into the project's code directory after immutable provenance checks.", _schema({"cell_id": {"type": "string"}, "revision_id": {"type": "string"}, "execution_id": {"type": "string"}, "path": {"type": "string"}, "strategy": {"type": "string", "enum": ["replace", "append", "create_only"], "default": "create_only"}, "base_sha256": {"type": "string", "description": "SHA-256 of the current target, required when appending to or replacing an existing file"}, "purpose": {"type": "string"}}, required=["cell_id", "revision_id", "execution_id", "path"]), lens="note", kind="async", risk="write", idempotency="non_idempotent"),
    ToolSpec("inspect_data_files", "List data files attached to this notebook with format, columns, and the R path to read each. Use this before reading an attached file when its path or schema is not already established.", lens="note"),
)

TOOL_REGISTRY = ToolSpecRegistry(WORKSPACE_TOOL_SPECS + ACTION_TOOL_SPECS + NOTE_TOOL_SPECS)


__all__ = [
    "ToolSpec",
    "ToolSpecRegistry",
    "TOOL_REGISTRY",
    "WORKSPACE_TOOL_SPECS",
    "ACTION_TOOL_SPECS",
    "NOTE_TOOL_SPECS",
]
