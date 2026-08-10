"""Failure classification and bounded retry policy for agent tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FAILURE_CLASSES = {
    "syntax_error",
    "missing_package",
    "missing_file",
    "timeout",
    "infrastructure",
    "validator",
    "execution_error",
    "unknown",
}

RETRYABLE_FAILURE_CLASSES = {"syntax_error", "validator", "execution_error"}
_SOURCE_EXTENSIONS = ("R", "r", "qmd", "yml", "yaml", "md")
_SOURCE_REFERENCE_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:R|r|qmd|yml|yaml|md))"
    r"(?:(?::|#L)(?P<line>\d+)(?:-(?P<end_line>\d+))?)?"
)
_REFERENCE_KEYS = {"file", "path", "source_file", "source", "page", "failed_page"}


@dataclass(frozen=True)
class RepairDiagnosis:
    """Deterministic routing information for an automatic project failure."""

    failure_class: str
    route: str
    repairable: bool
    reason: str
    context_mode: str
    file_references: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "route": self.route,
            "repairable": self.repairable,
            "reason": self.reason,
            "context_mode": self.context_mode,
            "file_references": [dict(reference) for reference in self.file_references],
        }


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _explicit_failure_class(observation: Any) -> str | None:
    """Read an explicit machine-generated failure role when one is present."""
    if isinstance(observation, dict):
        for key in ("failure_class", "failure_type", "category", "role", "step"):
            value = observation.get(key)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"validator", "validation", "validation_step", "artifacts"}:
                    return "validator"
                if lowered in {"timeout", "timed_out"}:
                    return "timeout"
                if lowered in {"infrastructure", "infra"}:
                    return "infrastructure"
                if lowered in {"missing_file", "file"}:
                    return "missing_file"
                if lowered in {"missing_package", "dependency"}:
                    return "missing_package"
        for value in observation.values():
            explicit = _explicit_failure_class(value)
            if explicit:
                return explicit
    elif isinstance(observation, (list, tuple, set)):
        for value in observation:
            explicit = _explicit_failure_class(value)
            if explicit:
                return explicit
    return None


def _line_number(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _source_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().strip("'\"(),;: ").replace("\\", "/")
    if candidate.startswith("file://"):
        candidate = candidate[7:]
    if not candidate.lower().endswith(
        tuple(f".{extension.lower()}" for extension in _SOURCE_EXTENSIONS)
    ):
        return None
    return candidate.lstrip("./") or None


def _extract_file_references(observation: Any) -> tuple[dict[str, Any], ...]:
    """Extract source locations without asking a model to locate the edit."""
    text = _text(observation)
    references: list[dict[str, Any]] = []

    def add(path: Any, line: Any = None, end_line: Any = None) -> None:
        normalized = _source_path(path)
        if not normalized:
            return
        start = _line_number(line)
        end = _line_number(end_line) or start
        if end is not None and start is not None and end < start:
            return
        reference: dict[str, Any] = {"path": normalized}
        if start is not None:
            reference["line"] = start
        if end is not None and end != start:
            reference["end_line"] = end
        if reference not in references:
            references.append(reference)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in _REFERENCE_KEYS:
                if key in value:
                    add(value.get(key), value.get("line"), value.get("end_line"))
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                walk(child)

    walk(observation)
    for match in _SOURCE_REFERENCE_RE.finditer(text):
        add(match.group("path"), match.group("line"), match.group("end_line"))
    return tuple(references)


def classify_tool_failure(observation: Any) -> str:
    """Classify an execution failure without asking an LLM to interpret it."""
    text = _text(observation).lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("timed out", "timeout", "time limit", "wall-clock")):
        return "timeout"
    explicit = _explicit_failure_class(observation)
    if explicit:
        return explicit
    if any(token in text for token in (
        "no package called", "package is not installed", "there is no package",
        "package is unavailable", "package is not available", "could not find function",
        "module not found", "library(", "namespace",
    )):
        return "missing_package"
    if any(token in text for token in (
        "no such file", "file does not exist", "does not exist",
        "cannot open", "file is missing", "missing file", "not found", "unknown path",
    )):
        return "missing_file"
    if any(token in text for token in (
        "provider unavailable", "rate limit", "quota", "connection refused",
        "connection reset", "redis", "database", "docker", "network error",
        "service unavailable", "temporarily unavailable",
    )):
        return "infrastructure"
    if any(token in text for token in (
        "parse error", "syntax error", "unexpected symbol", "unexpected token",
        "incomplete expression", "unterminated string",
    )):
        return "syntax_error"
    if any(token in text for token in (
        "validation failed", "validator", "invalid schema", "invalid value",
        "report validation", "schema violation",
    )):
        return "validator"
    if any(token in text for token in (
        "execution failed", "execution error", "error in", "failed to run",
        "command failed", "traceback",
    )):
        return "execution_error"
    return "unknown"


def diagnose_repair_failure(observation: Any) -> RepairDiagnosis:
    """Route a project failure before constructing any LLM repair prompt.

    Only semantic source failures receive model context. Dependency, missing-file,
    timeout, and infrastructure failures are returned to their owning policy or
    operator instead of being presented as a generic source-repair problem.
    """
    failure_class = classify_tool_failure(observation)
    references = _extract_file_references(observation)
    if failure_class == "syntax_error":
        return RepairDiagnosis(
            failure_class=failure_class,
            route="targeted_source",
            repairable=True,
            reason="A syntax failure may be repaired in the named source location.",
            context_mode="line_window" if any("line" in item for item in references) else "referenced_file",
            file_references=references,
        )
    if failure_class == "validator":
        return RepairDiagnosis(
            failure_class=failure_class,
            route="validator_diagnostic",
            repairable=True,
            reason="A validator failure may justify a bounded upstream source repair; the validator remains protected.",
            context_mode="line_window" if any("line" in item for item in references) else "relevant_source",
            file_references=references,
        )
    if failure_class == "execution_error":
        return RepairDiagnosis(
            failure_class=failure_class,
            route="targeted_source",
            repairable=True,
            reason="An execution error may justify a bounded semantic source repair.",
            context_mode="line_window" if any("line" in item for item in references) else "relevant_source",
            file_references=references,
        )
    routes = {
        "missing_package": (
            "dependency_policy",
            "The failure requires dependency policy or installation review, not an automatic source repair.",
        ),
        "missing_file": (
            "inspect_bindings",
            "The failure names a missing file; inspect project bindings and inputs before attempting source repair.",
        ),
        "timeout": (
            "resource_policy",
            "The failure exceeded a runtime budget; automatic source repair is not appropriate.",
        ),
        "infrastructure": (
            "infrastructure",
            "The failure is infrastructural; retry or repair the execution environment instead of source.",
        ),
    }
    route, reason = routes.get(
        failure_class,
        ("manual_review", "The failure could not be classified as a safe automatic source-repair case."),
    )
    return RepairDiagnosis(
        failure_class=failure_class,
        route=route,
        repairable=False,
        reason=reason,
        context_mode="none",
        file_references=references,
    )


def is_retryable_failure(failure_class: str) -> bool:
    return str(failure_class) in RETRYABLE_FAILURE_CLASSES


__all__ = [
    "FAILURE_CLASSES",
    "RETRYABLE_FAILURE_CLASSES",
    "RepairDiagnosis",
    "classify_tool_failure",
    "diagnose_repair_failure",
    "is_retryable_failure",
]
