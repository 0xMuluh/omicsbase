"""Decision-point registry loader and lookup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

_registry: dict[str, Any] | None = None


def load_registry() -> dict[str, Any]:
    """Load the decision-point registry from YAML."""
    global _registry
    if _registry is not None:
        return _registry

    path = Path(settings.registry_path)
    if not path.exists():
        logger.warning("Registry file not found at %s", path)
        return {"version": "0.0.0", "decision_points": {}}

    with open(path) as f:
        _registry = yaml.safe_load(f)

    return _registry


def get_decision_points() -> dict[str, Any]:
    """Return all decision points."""
    reg = load_registry()
    return reg.get("decision_points", {})


def get_contested_steps() -> list[str]:
    """Return IDs of all contested decision points."""
    points = get_decision_points()
    return [k for k, v in points.items() if v.get("classification") == "contested"]


def get_standard_steps() -> list[str]:
    """Return IDs of all standard decision points."""
    points = get_decision_points()
    return [k for k, v in points.items() if v.get("classification") == "standard"]


def get_ensemble_methods(step_id: str) -> list[dict] | None:
    """Return the default ensemble methods for a contested step."""
    points = get_decision_points()
    step = points.get(step_id, {})
    return step.get("default_ensemble")


def contested_ensemble_contract(step_id: str) -> dict[str, Any] | None:
    """Return the executable minimum ensemble contract for one decision point."""
    decision = get_decision_points().get(str(step_id), {})
    if decision.get("classification") != "contested":
        return None
    methods = [
        method for method in (decision.get("default_ensemble") or [])
        if isinstance(method, dict) and str(method.get("id") or "").strip()
    ]
    return {
        "required": True,
        "minimum_methods": 2,
        "method_ids": tuple(str(method["id"]) for method in methods),
    }


def validate_contested_ensemble(step_id: str, methods: list[dict] | None) -> list[str]:
    """Validate a contested workflow step before any source generation occurs."""
    contract = contested_ensemble_contract(step_id)
    if contract is None:
        return []
    supplied = methods or []
    ids = [str(item.get("id") or "").strip() for item in supplied if isinstance(item, dict)]
    errors: list[str] = []
    minimum = int(contract["minimum_methods"])
    if len(contract["method_ids"]) < minimum:
        errors.append(f"{step_id} registry contract defines fewer than {minimum} accepted methods")
    if len(ids) < minimum:
        errors.append(
            f"{step_id} requires an ensemble of at least {minimum} methods; received {len(ids)}"
        )
    if len(ids) != len(set(ids)):
        errors.append(f"{step_id} ensemble contains duplicate methods")
    allowed = set(contract["method_ids"])
    unknown = sorted(set(ids) - allowed)
    if unknown:
        errors.append(f"{step_id} ensemble contains unregistered methods: {', '.join(unknown)}")
    return errors


def format_registry_for_llm() -> str:
    """Format the registry as readable text for inclusion in LLM prompts."""
    reg = load_registry()
    return yaml.dump(reg, default_flow_style=False, sort_keys=False)
