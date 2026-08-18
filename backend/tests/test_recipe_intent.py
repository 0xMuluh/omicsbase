"""Tests for recipe-aware chat routing heuristics."""

from __future__ import annotations

from types import SimpleNamespace
from app.services.recipe_intent import infer_recipe_action, prefer_recipe_over_edit


def _microbiome_project():
    return SimpleNamespace(
        id="project-1",
        name="Recipe chat",
        question="Compare groups",
        notes=None,
        status="completed",
        agent_state="completed",
        agent_memory={},
        agent_actions=[],
        files=[],
        project_dir="/tmp/unused",
        study_manifest={"domain": "microbiome"},
        analysis_plan={
            "domain": "microbiome",
            "workflow": [
                {"recipe_id": "microbiome.inventory", "enabled": True, "parameters": {}},
                {
                    "recipe_id": "microbiome.alpha_diversity",
                    "enabled": True,
                    "parameters": {"metrics": ["observed", "shannon", "simpson"]},
                },
                {"recipe_id": "microbiome.beta_diversity", "enabled": True, "parameters": {}},
                {"recipe_id": "microbiome.permanova", "enabled": False, "parameters": {}},
            ],
        },
    )


def test_shannon_only_updates_alpha_metrics():
    decision = infer_recipe_action(
        _microbiome_project(),
        "For alpha diversity, use only Shannon (drop observed and simpson).",
    )
    assert decision is not None
    assert decision["action"] == "update_recipe_parameters"
    assert decision["arguments"]["recipe_id"] == "microbiome.alpha_diversity"
    assert decision["arguments"]["parameters"]["metrics"] == ["shannon"]


def test_enable_permanova():
    decision = infer_recipe_action(_microbiome_project(), "Enable PERMANOVA")
    assert decision is not None
    assert decision["action"] == "set_recipe_enabled"
    assert decision["arguments"] == {
        "recipe_id": "microbiome.permanova",
        "enabled": True,
    }


def test_rerun_beta_diversity():
    decision = infer_recipe_action(_microbiome_project(), "Rerun beta diversity")
    assert decision is not None
    assert decision["action"] == "run_recipe"
    assert decision["arguments"]["recipe_id"] == "microbiome.beta_diversity"


def test_caption_stays_source_edit():
    assert (
        infer_recipe_action(
            _microbiome_project(),
            "Add a caption under the alpha diversity plot",
        )
        is None
    )


def test_prefer_recipe_overrides_mistaken_edit():
    decision = prefer_recipe_over_edit(
        _microbiome_project(),
        "use only Shannon for alpha diversity",
        {
            "type": "action",
            "action": "edit_project",
            "instruction": "use only Shannon for alpha diversity",
            "message": "Editing source",
        },
    )
    assert decision["action"] == "update_recipe_parameters"
