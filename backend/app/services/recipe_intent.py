"""Heuristic routing from natural-language chat to recipe/config actions."""

from __future__ import annotations

import re
from typing import Any

from app.services.analysis_configuration import PARAMETER_OPTIONS
from app.services.recipe_registry import load_recipe_registry

# Narrative / layout / wording edits should stay on edit_project.
_SOURCE_EDIT_HINTS = (
    "caption",
    "title",
    "subtitle",
    "label",
    "heading",
    "font",
    "color",
    "colour",
    "theme",
    "layout",
    "wording",
    "typo",
    "grammar",
    "rephrase",
    "rewrite the text",
    "add a note",
    "footnote",
    "axis label",
    "legend title",
)

_ENABLE_RE = re.compile(
    r"\b(enable|add|include|turn on|switch on|activate)\b",
    re.I,
)
_DISABLE_RE = re.compile(
    r"\b(disable|remove|exclude|skip|turn off|switch off|deactivate|drop the)\b",
    re.I,
)
_RERUN_RE = re.compile(
    r"\b(re-?run|rerun|run again|recompute|recalculate|refresh)\b",
    re.I,
)
_ONLY_RE = re.compile(r"\bonly\b", re.I)
_DROP_METRICS_RE = re.compile(r"\b(drop|remove|without|exclude)\b", re.I)


def infer_recipe_action(project, message: str) -> dict[str, Any] | None:
    """Return a recipe/config action decision when the utterance is clearly recipe-level.

    Returns None when the request is ambiguous or should remain a source edit.
    """
    text = " ".join((message or "").strip().split())
    if not text:
        return None
    lowered = text.lower()

    if _looks_like_source_edit(lowered):
        return None

    recipe = _resolve_recipe(project, lowered)
    if recipe is None:
        return None

    recipe_id = recipe["id"]
    parameters = _extract_parameters(recipe_id, lowered, text)
    if parameters:
        return {
            "type": "action",
            "action": "update_recipe_parameters",
            "arguments": {"recipe_id": recipe_id, "parameters": parameters},
            "message": (
                f"I’ll update {recipe.get('name') or recipe_id} parameters "
                f"({', '.join(sorted(parameters))}) and re-run that analysis."
            ),
        }

    if _ENABLE_RE.search(lowered) and not _DISABLE_RE.search(lowered):
        return {
            "type": "action",
            "action": "set_recipe_enabled",
            "arguments": {"recipe_id": recipe_id, "enabled": True},
            "message": f"I’ll enable {recipe.get('name') or recipe_id} and generate that analysis.",
        }

    if _DISABLE_RE.search(lowered) and not parameters:
        # "drop shannon" is a param change handled above; "skip permanova" disables.
        if any(token in lowered for token in ("recipe", "analysis", "permanova", "limrots", "ordination", "alpha", "beta", "scan", "mixed model")):
            return {
                "type": "action",
                "action": "set_recipe_enabled",
                "arguments": {"recipe_id": recipe_id, "enabled": False},
                "message": f"I’ll disable {recipe.get('name') or recipe_id} and refresh the report plan.",
            }

    if _RERUN_RE.search(lowered):
        return {
            "type": "action",
            "action": "run_recipe",
            "arguments": {"recipe_id": recipe_id},
            "message": f"I’ll re-run {recipe.get('name') or recipe_id} with the current configuration.",
        }

    return None


def prefer_recipe_over_edit(project, message: str, decision: dict[str, Any]) -> dict[str, Any]:
    """If the model chose edit_project for a clear recipe ask, rewrite to a config action."""
    if not isinstance(decision, dict):
        return decision
    if decision.get("type") != "action" or decision.get("action") != "edit_project":
        return decision
    inferred = infer_recipe_action(project, message)
    return inferred or decision


def _looks_like_source_edit(lowered: str) -> bool:
    return any(hint in lowered for hint in _SOURCE_EDIT_HINTS)


def _resolve_recipe(project, lowered: str) -> dict[str, Any] | None:
    domain = (project.analysis_plan or {}).get("domain") or (project.study_manifest or {}).get("domain")
    registry = load_recipe_registry()["recipes"]
    candidates: list[tuple[int, dict[str, Any]]] = []

    for recipe_id, recipe in registry.items():
        if domain and recipe.get("domain") != domain:
            continue
        score = 0
        names = {
            recipe_id.lower(),
            recipe_id.split(".", 1)[-1].lower(),
            str(recipe.get("name") or "").lower(),
            *[str(alias).lower() for alias in recipe.get("aliases") or []],
        }
        # Prefer longer / more specific alias hits.
        for name in sorted(names, key=len, reverse=True):
            if not name:
                continue
            if name in lowered or name.replace("_", " ") in lowered or name.replace("-", " ") in lowered:
                score = max(score, len(name))
        if "differential abundance" in lowered and "limrots" in recipe_id:
            score = max(score, 22)
        if "alpha" in lowered and recipe_id.endswith("alpha_diversity"):
            score = max(score, 12)
        if "beta" in lowered and recipe_id.endswith("beta_diversity"):
            score = max(score, 12)
        if score:
            candidates.append((score, {"id": recipe_id, **recipe}))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _extract_parameters(recipe_id: str, lowered: str, original: str) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    for (rid, key), options in PARAMETER_OPTIONS.items():
        if rid != recipe_id:
            continue
        mentioned = [option for option in options if str(option).lower() in lowered]
        if not mentioned:
            continue

        if key == "metrics":
            defaults = ["observed", "shannon", "simpson"]
            if _ONLY_RE.search(lowered):
                after_only = lowered.split("only", 1)[1]
                keep_span = re.split(
                    r"\(|\bdrop\b|\bremove\b|\bwithout\b|\bexclude\b",
                    after_only,
                    maxsplit=1,
                )[0]
                keep = [option for option in options if str(option).lower() in keep_span]
                if keep:
                    updates[key] = keep
                    continue
            if _DROP_METRICS_RE.search(lowered) and mentioned:
                drop = {str(item).lower() for item in mentioned}
                # If the utterance also names keepers after "use", prefer drop semantics
                # against the default metric set.
                remaining = [item for item in defaults if item not in drop]
                if remaining and remaining != defaults:
                    updates[key] = remaining
                    continue
            if mentioned:
                updates[key] = mentioned
            continue

        # Single-choice parameters: take the last mentioned option.
        if len(options) > 1 or mentioned:
            updates[key] = mentioned[-1]

    # Numeric params commonly requested in chat.
    for key, pattern in (
        ("permutations", r"\b(\d{2,5})\s+permutations?\b"),
        ("bootstrap_iterations", r"\b(\d{2,5})\s+bootstraps?\b"),
        ("seed", r"\bseed\s*(?:to|=|:)?\s*(\d+)\b"),
        ("min_prevalence", r"\bprevalence\s*(?:to|=|:)?\s*(0?\.\d+|\d+(?:\.\d+)?)\b"),
    ):
        if (recipe_id, key) in PARAMETER_OPTIONS:
            continue
        match = re.search(pattern, original, flags=re.I)
        if not match:
            continue
        # Only attach if this recipe declares the parameter.
        recipe = load_recipe_registry()["recipes"].get(recipe_id) or {}
        if key not in (recipe.get("parameters") or {}):
            continue
        raw = match.group(1)
        updates[key] = float(raw) if "." in raw else int(raw)

    return updates
