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


def format_registry_for_llm() -> str:
    """Format the registry as readable text for inclusion in LLM prompts."""
    reg = load_registry()
    return yaml.dump(reg, default_flow_style=False, sort_keys=False)
