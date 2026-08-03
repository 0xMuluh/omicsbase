"""Typed analysis recipe registry."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import settings

REGISTRY_PATH = Path(settings.registry_path).with_name("recipes.yaml")


@lru_cache(maxsize=1)
def load_recipe_registry() -> dict[str, Any]:
    """Load and minimally validate the repository recipe registry."""
    data = yaml.safe_load(REGISTRY_PATH.read_text()) or {}
    recipes = data.get("recipes")
    if not isinstance(recipes, dict) or not recipes:
        raise ValueError("Recipe registry must define at least one recipe")

    for recipe_id, recipe in recipes.items():
        if not isinstance(recipe, dict):
            raise ValueError(f"Recipe {recipe_id} must be a mapping")
        if recipe.get("domain") not in {"microbiome", "metabolomics"}:
            raise ValueError(f"Recipe {recipe_id} has an unsupported domain")
        recipe.setdefault("id", recipe_id)
        recipe.setdefault("aliases", [])
        recipe.setdefault("depends_on", [])
        recipe.setdefault("r_packages", [])
        recipe.setdefault("parameters", {})
        recipe.setdefault("outputs", [])
    return data


def get_recipe(recipe_id: str) -> dict[str, Any] | None:
    """Return one recipe definition."""
    recipe = load_recipe_registry()["recipes"].get(recipe_id)
    return dict(recipe) if recipe else None


def resolve_recipe(step_id: str, domain: str) -> dict[str, Any] | None:
    """Resolve a workflow step or explicit recipe ID within a domain."""
    recipes = load_recipe_registry()["recipes"]
    explicit = recipes.get(step_id)
    if explicit and explicit.get("domain") == domain:
        return dict(explicit)

    normalized = step_id.lower().replace("-", "_").replace(" ", "_")
    for recipe_id, recipe in recipes.items():
        aliases = {
            alias.lower().replace("-", "_").replace(" ", "_")
            for alias in recipe.get("aliases", [])
        }
        if recipe.get("domain") == domain and normalized in aliases:
            resolved = dict(recipe)
            resolved["id"] = recipe_id
            return resolved
    return None


def format_recipes_for_llm() -> str:
    """Format the executable recipe surface for the planner."""
    lines = []
    for recipe_id, recipe in load_recipe_registry()["recipes"].items():
        lines.append(
            f"- {recipe_id}: domain={recipe['domain']}; name={recipe.get('name')}; "
            f"aliases={recipe.get('aliases', [])}; parameters={recipe.get('parameters', {})}; "
            f"r_packages={recipe.get('r_packages', [])}; outputs={recipe.get('outputs', [])}"
        )
    return "\n".join(lines)
