"""Validated mutations of the canonical analysis graph."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.recipe_registry import get_recipe, load_recipe_registry

PARAMETER_OPTIONS = {
    ("microbiome.inventory", "feature_orientation"): {
        "auto",
        "features_by_samples",
        "samples_by_features",
    },
    ("microbiome.inventory", "input_scale"): {"auto"},
    ("microbiome.alpha_diversity", "metrics"): {"observed", "shannon", "simpson"},
    ("microbiome.alpha_diversity", "fdr_method"): {"BH", "BY", "holm", "bonferroni"},
    ("microbiome.beta_diversity", "distance"): {"bray", "jaccard", "euclidean", "manhattan"},
    ("microbiome.beta_diversity", "ordination"): {"pcoa"},
    ("metabolomics.inventory", "feature_orientation"): {"samples_by_features"},
    ("metabolomics.inventory", "transform"): {"log_z"},
    ("metabolomics.linear_feature_scan", "fdr_method"): {"BH", "BY", "holm", "bonferroni"},
    ("metabolomics.repeated_measures_mixed_model", "fdr_method"): {
        "BH",
        "BY",
        "holm",
        "bonferroni",
    },
}


def apply_analysis_configuration(
    project,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Return a validated plan mutation without touching generated source."""
    if not project.analysis_plan:
        raise ValueError("The project has no analysis plan to configure.")

    previous_plan = deepcopy(project.analysis_plan)
    plan = deepcopy(project.analysis_plan)

    if action == "set_recipe_enabled":
        recipe = _validated_recipe(plan, str(arguments.get("recipe_id") or ""))
        enabled = arguments.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("set_recipe_enabled requires a boolean 'enabled' value.")
        step = _find_or_create_step(plan, recipe)
        step["enabled"] = enabled
        summary = f"{'Enabled' if enabled else 'Disabled'} {recipe['name']}"

    elif action == "update_recipe_parameters":
        recipe = _validated_recipe(plan, str(arguments.get("recipe_id") or ""))
        updates = arguments.get("parameters")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("update_recipe_parameters requires a non-empty parameters object.")
        validated = _validate_parameters(recipe, updates)
        step = _find_or_create_step(plan, recipe)
        step["parameters"] = {**(step.get("parameters") or {}), **validated}
        step["enabled"] = True
        summary = f"Updated {recipe['name']} parameters: {', '.join(sorted(validated))}"

    elif action == "set_analysis_variables":
        summary = _set_analysis_variables(project, plan, arguments)

    elif action == "rollback_analysis_configuration":
        rollback_plan = _latest_previous_plan(project.agent_actions or [])
        if rollback_plan is None:
            raise ValueError("No previous agent analysis configuration is available to restore.")
        plan = rollback_plan
        summary = "Restored the previous analysis configuration"

    else:
        raise ValueError(f"Unsupported analysis configuration action: {action}")

    plan["recipe_registry_version"] = load_recipe_registry()["version"]
    return {
        "summary": summary,
        "previous_plan": previous_plan,
        "plan": plan,
    }


def _validated_recipe(plan: dict[str, Any], recipe_id: str) -> dict[str, Any]:
    if not recipe_id:
        raise ValueError("A recipe_id is required.")
    recipe = get_recipe(recipe_id)
    if not recipe:
        raise ValueError(f"Unknown recipe: {recipe_id}")
    domain = plan.get("domain")
    if recipe.get("domain") != domain:
        raise ValueError(
            f"Recipe {recipe_id} belongs to {recipe.get('domain')}, not project domain {domain}."
        )
    return recipe


def _find_or_create_step(plan: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    workflow = plan.setdefault("workflow", [])
    for step in workflow:
        if step.get("recipe_id") == recipe["id"]:
            return step

    existing_ids = {step.get("id") for step in workflow}
    base_id = recipe["id"].split(".", 1)[-1]
    step_id = base_id
    suffix = 2
    while step_id in existing_ids:
        step_id = f"{base_id}_{suffix}"
        suffix += 1
    step = {
        "id": step_id,
        "name": recipe.get("name") or base_id.replace("_", " ").title(),
        "classification": recipe.get("classification", "standard"),
        "recipe_id": recipe["id"],
        "enabled": True,
        "rationale": "Configured through the workspace agent.",
        "ensemble_methods": None,
        "parameters": {},
    }
    workflow.append(step)
    return step


def _validate_parameters(recipe: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    defaults = recipe.get("parameters") or {}
    unknown = sorted(set(updates) - set(defaults))
    if unknown:
        raise ValueError(
            f"Unknown parameter(s) for {recipe['id']}: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(defaults)) or 'none'}."
        )

    validated: dict[str, Any] = {}
    for key, value in updates.items():
        default = defaults[key]
        if not _matches_parameter_type(default, value):
            raise ValueError(
                f"Parameter {key} for {recipe['id']} must match the type of its default value "
                f"({type(default).__name__})."
            )
        options = PARAMETER_OPTIONS.get((recipe["id"], key))
        if options:
            supplied = set(value) if isinstance(value, list) else {value}
            unsupported = supplied - options
            if unsupported:
                raise ValueError(
                    f"Unsupported value(s) for {recipe['id']}.{key}: "
                    f"{', '.join(sorted(map(str, unsupported)))}. "
                    f"Allowed: {', '.join(sorted(map(str, options)))}."
                )
        if key in {
            "permutations",
            "seed",
            "bootstrap_iterations",
            "min_complete_cases",
            "min_total_abundance",
        } and isinstance(value, (int, float)) and value < 0:
            raise ValueError(f"Parameter {key} cannot be negative.")
        if key in {"permutations", "bootstrap_iterations", "min_complete_cases"} and value == 0:
            raise ValueError(f"Parameter {key} must be greater than zero.")
        if key == "min_prevalence" and not 0 <= value <= 1:
            raise ValueError("Parameter min_prevalence must be between 0 and 1.")
        validated[key] = value
    return validated


def _matches_parameter_type(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return value is None or isinstance(value, type(default))


def _set_analysis_variables(
    project,
    plan: dict[str, Any],
    arguments: dict[str, Any],
) -> str:
    available_columns = {
        str(column)
        for file_record in (project.study_manifest or {}).get("files", [])
        for column in file_record.get("columns", [])
    }

    changed: list[str] = []
    if "grouping_variable" in arguments:
        grouping = arguments.get("grouping_variable")
        if grouping is not None and grouping not in available_columns:
            raise ValueError(f"Grouping variable does not exist in uploaded study columns: {grouping}")
        plan["grouping_variable"] = grouping
        changed.append("grouping variable")
        if "group_levels" not in arguments:
            candidate = next(
                (
                    item
                    for item in (project.study_manifest or {}).get("grouping_candidates", [])
                    if item.get("column") == grouping
                ),
                None,
            )
            plan["group_levels"] = (candidate or {}).get("levels") or []

    if "group_levels" in arguments:
        levels = arguments.get("group_levels")
        if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
            raise ValueError("group_levels must be a list of strings.")
        plan["group_levels"] = levels
        changed.append("group levels")

    if "covariates" in arguments:
        covariates = arguments.get("covariates")
        if not isinstance(covariates, list) or not all(isinstance(item, str) for item in covariates):
            raise ValueError("covariates must be a list of column names.")
        missing = sorted(set(covariates) - available_columns)
        if missing:
            raise ValueError(f"Covariate column(s) not found in uploaded study: {', '.join(missing)}")
        grouping = plan.get("grouping_variable")
        plan["covariates"] = [item for item in dict.fromkeys(covariates) if item != grouping]
        changed.append("covariates")

    if not changed:
        raise ValueError("No analysis variable changes were supplied.")
    return f"Updated {', '.join(changed)}"


def _latest_previous_plan(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for action in reversed(actions):
        if action.get("type") != "analysis_config" or action.get("status") != "completed":
            continue
        previous_plan = (action.get("details") or {}).get("previous_plan")
        if isinstance(previous_plan, dict):
            return deepcopy(previous_plan)
    return None
