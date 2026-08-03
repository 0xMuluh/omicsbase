"""Dependency-aware targeted recipe execution with content-addressed caching."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import yaml

from app.services.recipe_engine import recipe_page_path
from app.services.recipe_registry import get_recipe, load_recipe_registry
from app.services.runner import run_project

CACHE_VERSION = 1
CACHE_PATH = Path("output/derived/.omicsbase_execution_cache.json")


async def run_recipe_target(
    project_dir: str,
    recipe_id: str,
    progress_callback: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Execute only a recipe's stale dependency closure."""
    base = Path(project_dir)
    code_dir = base / "code"
    config_path = code_dir / "study_config.yml"
    if not config_path.exists():
        return _failed(f"Missing recipe configuration: {config_path}")

    config = yaml.safe_load(config_path.read_text()) or {}
    enabled_recipes = (config.get("analyses") or {}).get("recipes") or []
    if recipe_id not in enabled_recipes:
        return _failed(f"Recipe is not enabled in this project: {recipe_id}")
    if not get_recipe(recipe_id):
        return _failed(f"Unknown recipe: {recipe_id}")

    closure = _dependency_closure(recipe_id)
    cache = _load_cache(base)
    data_fingerprint = _data_fingerprint(base, config)
    data_outputs_exist = _recipe_outputs_exist(base, closure[0], include_page=False)
    data_cache_hit = (
        cache.get("data", {}).get("fingerprint") == data_fingerprint
        and data_outputs_exist
    )

    fingerprints: dict[str, str] = {}
    stale_recipes: list[str] = []
    cache_hits: list[str] = []
    for current_recipe_id in closure:
        dependency_fingerprints = {
            dependency: fingerprints[dependency]
            for dependency in (get_recipe(current_recipe_id) or {}).get("depends_on", [])
            if dependency in fingerprints
        }
        fingerprint = _recipe_fingerprint(
            base,
            config,
            current_recipe_id,
            data_fingerprint,
            dependency_fingerprints,
        )
        fingerprints[current_recipe_id] = fingerprint
        cache_hit = (
            data_cache_hit
            and cache.get("recipes", {}).get(current_recipe_id) == fingerprint
            and _recipe_outputs_exist(base, current_recipe_id, include_page=True)
        )
        if cache_hit:
            cache_hits.append(current_recipe_id)
            if progress_callback:
                progress_callback(
                    f"recipe_{_safe_id(current_recipe_id)}",
                    "completed",
                    f"Cache hit: {current_recipe_id}",
                )
        else:
            stale_recipes.append(current_recipe_id)

    pages = [
        page
        for current_recipe_id in stale_recipes
        if (page := recipe_page_path(current_recipe_id))
    ]
    if not pages and data_cache_hit:
        return {
            "status": "completed",
            "target_recipe": recipe_id,
            "executed_recipes": [],
            "cache_hits": cache_hits,
            "data_cache_hit": True,
            "pages": [],
            "logs": ["All targeted recipe outputs are current."],
            "errors": [],
        }

    result = await run_project(
        project_dir=project_dir,
        progress_callback=progress_callback,
        run_data=not data_cache_hit,
        target_pages=pages,
    )
    result["target_recipe"] = recipe_id
    result["executed_recipes"] = stale_recipes
    result["cache_hits"] = cache_hits
    result["data_cache_hit"] = data_cache_hit

    if result["status"] == "completed":
        cache["version"] = CACHE_VERSION
        cache["registry_version"] = load_recipe_registry()["version"]
        cache["data"] = {"fingerprint": data_fingerprint}
        recipe_cache = cache.setdefault("recipes", {})
        recipe_cache.update(fingerprints)
        _write_cache(base, cache)
    return result


def invalidate_recipe_cache(project_dir: str, recipe_id: str | None = None) -> None:
    """Invalidate one recipe fingerprint or the complete execution cache."""
    base = Path(project_dir)
    if recipe_id is None:
        (base / CACHE_PATH).unlink(missing_ok=True)
        return
    cache = _load_cache(base)
    (cache.get("recipes") or {}).pop(recipe_id, None)
    _write_cache(base, cache)


def _dependency_closure(recipe_id: str) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(current: str) -> None:
        if current in visiting:
            raise ValueError(f"Recipe dependency cycle detected at {current}")
        if current in ordered:
            return
        recipe = get_recipe(current)
        if not recipe:
            raise ValueError(f"Unknown recipe dependency: {current}")
        visiting.add(current)
        for dependency in recipe.get("depends_on", []):
            visit(dependency)
        visiting.remove(current)
        ordered.append(current)

    visit(recipe_id)
    return ordered


def _data_fingerprint(base: Path, config: dict[str, Any]) -> str:
    analyses = config.get("analyses") or {}
    inventory_recipe = next(
        (
            recipe_id
            for recipe_id in analyses.get("recipes", [])
            if recipe_id.endswith(".inventory")
        ),
        None,
    )
    contract = {
        "paths": config.get("paths"),
        "identifiers": config.get("identifiers"),
        "features": config.get("features"),
        "inventory_parameters": (analyses.get("recipe_parameters") or {}).get(inventory_recipe),
    }
    digest = hashlib.sha256(_canonical_json(contract))
    for relative_path in ("code/data.R", "code/recipe_runtime.R"):
        _update_file_digest(digest, base / relative_path)
    for configured_path in (config.get("paths") or {}).values():
        if not configured_path:
            continue
        _update_file_digest(digest, (base / "code" / str(configured_path)).resolve())
    return digest.hexdigest()


def _recipe_fingerprint(
    base: Path,
    config: dict[str, Any],
    recipe_id: str,
    data_fingerprint: str,
    dependency_fingerprints: dict[str, str],
) -> str:
    analyses = config.get("analyses") or {}
    payload = {
        "cache_version": CACHE_VERSION,
        "registry_version": load_recipe_registry()["version"],
        "recipe_id": recipe_id,
        "parameters": (analyses.get("recipe_parameters") or {}).get(recipe_id) or {},
        "variables": config.get("variables") or {},
        "data_fingerprint": data_fingerprint,
        "dependencies": dependency_fingerprints,
    }
    digest = hashlib.sha256(_canonical_json(payload))
    page = recipe_page_path(recipe_id)
    if page:
        _update_file_digest(digest, base / "code" / page)
    return digest.hexdigest()


def _recipe_outputs_exist(
    base: Path,
    recipe_id: str,
    *,
    include_page: bool,
) -> bool:
    recipe = get_recipe(recipe_id)
    if not recipe:
        return False
    outputs = [base / path for path in recipe.get("outputs", [])]
    if include_page:
        page = recipe_page_path(recipe_id)
        if page:
            outputs.append(base / "output" / Path(page).with_suffix(".html"))
    return bool(outputs) and all(path.exists() for path in outputs)


def _load_cache(base: Path) -> dict[str, Any]:
    path = base / CACHE_PATH
    if not path.exists():
        return {"version": CACHE_VERSION, "recipes": {}}
    try:
        cache = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "recipes": {}}
    if cache.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "recipes": {}}
    return cache


def _write_cache(base: Path, cache: dict[str, Any]) -> None:
    path = base / CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _update_file_digest(digest, path: Path) -> None:
    digest.update(str(path).encode())
    if not path.exists() or not path.is_file():
        digest.update(b"<missing>")
        return
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value)


def _failed(message: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "logs": [],
        "errors": [{"step": "recipe_execution", "error": message}],
        "pages": [],
    }
