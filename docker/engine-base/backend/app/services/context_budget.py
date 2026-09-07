"""Bounded, valid JSON context for model-facing agent prompts.

Context limits must not be enforced by slicing the serialized JSON string: a
slice can remove a closing quote/brace and silently discard the fields that
matter most to a scientific decision.  This module compacts values before
serialization, keeps preferred top-level fields first, and always returns a
complete JSON document.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def bounded_json(
    value: Any,
    max_chars: int,
    *,
    priority_keys: Sequence[str] = (),
) -> str:
    """Serialize ``value`` as valid JSON within ``max_chars`` when possible.

    Large values are compacted structurally rather than cut at an arbitrary
    character offset.  Top-level ``priority_keys`` are retained before lower
    priority fields.  If a hostile or unusually small budget cannot retain
    useful content, the final fallback is still a valid JSON object.
    """

    limit = max(64, int(max_chars))
    safe = _json_safe(value)
    rendered = _dump(safe)
    if len(rendered) <= limit:
        return rendered

    preferred = tuple(str(key) for key in priority_keys)
    compact = _compact_root(safe, limit, preferred_keys=preferred)
    rendered = _dump(compact)
    if len(rendered) <= limit:
        return rendered

    # A final root-level pass guarantees that a pathological nested value
    # cannot make us return malformed JSON or exceed the advertised budget.
    if isinstance(safe, Mapping):
        root: dict[str, Any] = {"_context_truncated": True}
        ordered = [
            *[key for key in preferred if key in safe],
            *[str(key) for key in safe if str(key) not in preferred],
        ]
        for key in ordered:
            remaining = limit - len(_dump(root)) - len(str(key)) - 8
            if remaining < 24:
                break
            candidate = _compact_value(safe[key], remaining)
            trial = dict(root)
            trial[str(key)] = candidate
            if len(_dump(trial)) <= limit:
                root = trial
        rendered = _dump(root)
        if len(rendered) <= limit:
            return rendered

    return _dump({"_context_truncated": True})


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=1,
        sort_keys=True,
        separators=(",", ": "),
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, Path):
        return value.as_posix()
    return str(value)


def _compact_value(
    value: Any,
    budget: int,
    *,
    preferred_keys: Sequence[str] = (),
) -> Any:
    budget = max(24, int(budget))
    if len(_dump(value)) <= budget:
        return value

    if isinstance(value, str):
        marker = " …[truncated]"
        keep = max(1, budget - len(_dump(marker)) - 8)
        return value[:keep] + marker

    if isinstance(value, Mapping):
        output: dict[str, Any] = {"_context_truncated": True}
        preferred = tuple(str(key) for key in preferred_keys)
        ordered = [
            *[key for key in preferred if key in value],
            *[str(key) for key in value if str(key) not in preferred],
        ]
        for key in ordered:
            remaining = budget - len(_dump(output)) - len(str(key)) - 8
            if remaining < 24:
                break
            child = _compact_value(value[key], remaining)
            trial = dict(output)
            trial[str(key)] = child
            if len(_dump(trial)) <= budget:
                output = trial
        return output

    if isinstance(value, list):
        output: list[Any] = []
        for index, item in enumerate(value):
            remaining = budget - len(_dump(output)) - 12
            if remaining < 24:
                break
            child = _compact_value(item, remaining)
            trial = [*output, child]
            if len(_dump(trial)) > budget:
                break
            output.append(child)
        if len(output) < len(value):
            marker = {"_context_truncated": True, "item_count": len(value)}
            trial = [*output, marker]
            if len(_dump(trial)) <= budget:
                return trial
        return output or {"_context_truncated": True, "item_count": len(value)}

    return value


def _compact_root(value: Any, budget: int, *, preferred_keys: Sequence[str]) -> Any:
    """Compact the root while reserving room for each preferred field."""
    if not isinstance(value, Mapping):
        return _compact_value(value, budget)

    output: dict[str, Any] = {"_context_truncated": True}
    preferred = [str(key) for key in preferred_keys if key in value]
    remaining_preferred = len(preferred)
    for key in preferred:
        remaining = budget - len(_dump(output)) - len(key) - 8
        share = max(48, remaining // max(1, remaining_preferred))
        candidate = _compact_value(value[key], share)
        trial = dict(output)
        trial[key] = candidate
        if len(_dump(trial)) <= budget:
            output = trial
        remaining_preferred -= 1

    for key in (str(key) for key in value if str(key) not in preferred):
        remaining = budget - len(_dump(output)) - len(key) - 8
        if remaining < 48:
            break
        candidate = _compact_value(value[key], remaining)
        trial = dict(output)
        trial[key] = candidate
        if len(_dump(trial)) <= budget:
            output = trial
    return output


__all__ = ["bounded_json"]
