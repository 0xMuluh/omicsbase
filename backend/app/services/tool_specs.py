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


# Workspace coding runs through OpenCode (read/edit/bash + ask_user MCP), not
# in-process OmicsBase tools.
WORKSPACE_TOOL_SPECS: tuple[ToolSpec, ...] = ()


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
