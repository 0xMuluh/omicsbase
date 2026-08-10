"""Failure classification and bounded retry policy for agent tools."""

from __future__ import annotations

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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def classify_tool_failure(observation: Any) -> str:
    """Classify an execution failure without asking an LLM to interpret it."""
    text = _text(observation).lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("timed out", "timeout", "time limit", "wall-clock")):
        return "timeout"
    if any(token in text for token in (
        "no package called", "package is not installed", "there is no package",
        "could not find function", "module not found", "library(", "namespace",
    )):
        return "missing_package"
    if any(token in text for token in (
        "no such file", "file does not exist", "does not exist",
        "cannot open", "not found", "unknown path",
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


def is_retryable_failure(failure_class: str) -> bool:
    return str(failure_class) in RETRYABLE_FAILURE_CLASSES


__all__ = [
    "FAILURE_CLASSES",
    "RETRYABLE_FAILURE_CLASSES",
    "classify_tool_failure",
    "is_retryable_failure",
]
