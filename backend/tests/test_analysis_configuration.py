"""Tests for guarded workspace-agent analysis mutations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.analysis_configuration import apply_analysis_configuration


def _project():
    return SimpleNamespace(
        analysis_plan={
            "project_name": "Configuration test",
            "domain": "microbiome",
            "study_type": "two_group_comparison",
            "question": "Compare groups",
            "grouping_variable": "condition",
            "group_levels": ["Control", "Treatment"],
            "covariates": [],
            "workflow": [
                {
                    "id": "alpha_diversity",
                    "name": "Alpha diversity",
                    "classification": "standard",
                    "recipe_id": "microbiome.alpha_diversity",
                    "enabled": True,
                    "parameters": {},
                }
            ],
        },
        study_manifest={
            "files": [
                {
                    "name": "metadata.csv",
                    "columns": ["sample_id", "condition", "age", "batch"],
                }
            ],
            "grouping_candidates": [
                {
                    "file": "metadata.csv",
                    "column": "condition",
                    "levels": ["Control", "Treatment"],
                }
            ],
        },
        agent_actions=[],
    )


def test_updates_only_registered_recipe_parameters():
    mutation = apply_analysis_configuration(
        _project(),
        "update_recipe_parameters",
        {
            "recipe_id": "microbiome.alpha_diversity",
            "parameters": {"metrics": ["observed", "shannon"]},
        },
    )

    step = mutation["plan"]["workflow"][0]
    assert step["parameters"] == {"metrics": ["observed", "shannon"]}
    assert mutation["previous_plan"]["workflow"][0]["parameters"] == {}


def test_rejects_unknown_recipe_parameter():
    with pytest.raises(ValueError, match="Unknown parameter"):
        apply_analysis_configuration(
            _project(),
            "update_recipe_parameters",
            {
                "recipe_id": "microbiome.alpha_diversity",
                "parameters": {"invented_threshold": 42},
            },
        )


def test_validates_grouping_and_covariate_columns():
    mutation = apply_analysis_configuration(
        _project(),
        "set_analysis_variables",
        {
            "grouping_variable": "condition",
            "covariates": ["age", "batch"],
        },
    )
    assert mutation["plan"]["covariates"] == ["age", "batch"]

    with pytest.raises(ValueError, match="not found"):
        apply_analysis_configuration(
            _project(),
            "set_analysis_variables",
            {"covariates": ["imaginary_column"]},
        )


def test_can_enable_registered_recipe_not_yet_in_plan():
    mutation = apply_analysis_configuration(
        _project(),
        "set_recipe_enabled",
        {
            "recipe_id": "microbiome.permanova",
            "enabled": True,
        },
    )
    permanova = next(
        step
        for step in mutation["plan"]["workflow"]
        if step["recipe_id"] == "microbiome.permanova"
    )
    assert permanova["enabled"] is True


def test_rolls_back_to_previous_recorded_plan():
    project = _project()
    previous = {
        **project.analysis_plan,
        "grouping_variable": "batch",
        "group_levels": ["One", "Two"],
    }
    project.agent_actions = [
        {
            "type": "analysis_config",
            "status": "completed",
            "details": {"previous_plan": previous},
        }
    ]

    mutation = apply_analysis_configuration(
        project,
        "rollback_analysis_configuration",
        {},
    )
    assert mutation["plan"]["grouping_variable"] == "batch"
