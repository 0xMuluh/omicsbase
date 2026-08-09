"""Tests for dependency-aware targeted recipe execution caching."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services import recipe_execution
from app.services.recipe_registry import get_recipe


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    code = project / "code"
    data = project / "data"
    code.mkdir(parents=True)
    data.mkdir()
    (data / "counts.csv").write_text("taxon,S1,S2\nA,1,2\n")
    (data / "metadata.csv").write_text("sample_id,group\nS1,A\nS2,B\n")
    (code / "data.R").write_text("# deterministic data loader\n")
    (code / "recipe_runtime.R").write_text("# runtime\n")
    for page in (
        "data/data_summary.qmd",
        "primary/beta_diversity.qmd",
        "primary/permanova.qmd",
    ):
        path = code / page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n")
    config = {
        "paths": {
            "feature_table": "../data/counts.csv",
            "metadata": "../data/metadata.csv",
        },
        "identifiers": {"sample_id": "sample_id", "feature_id": "taxon"},
        "features": {"orientation": "auto"},
        "variables": {"grouping": "group", "group_levels": ["A", "B"], "covariates": []},
        "analyses": {
            "recipes": [
                "microbiome.inventory",
                "microbiome.beta_diversity",
                "microbiome.permanova",
            ],
            "recipe_parameters": {
                "microbiome.inventory": {
                    "feature_orientation": "auto",
                    "input_scale": "auto",
                    "min_prevalence": 0.1,
                    "min_total_abundance": 0,
                },
                "microbiome.beta_diversity": {
                    "distance": "bray",
                    "ordination": "pcoa",
                },
                "microbiome.permanova": {
                    "permutations": 999,
                    "seed": 1,
                },
            },
        },
    }
    (code / "study_config.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    return project


def _materialize_outputs(project: Path, recipe_ids: list[str]) -> None:
    for recipe_id in recipe_ids:
        for output in (get_recipe(recipe_id) or {}).get("outputs", []):
            path = project / output
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("result\n")
    for page in (
        "data/data_summary.html",
        "beta/beta.html",
        "beta/permanova.html",
    ):
        path = project / "output" / page
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>")


@pytest.mark.asyncio
async def test_targeted_execution_reuses_unchanged_dependency_closure(tmp_path, monkeypatch):
    project = _project(tmp_path)
    calls = []

    async def fake_run_project(**kwargs):
        calls.append(kwargs)
        _materialize_outputs(
            project,
            [
                "microbiome.inventory",
                "microbiome.beta_diversity",
                "microbiome.permanova",
            ],
        )
        return {"status": "completed", "logs": [], "errors": [], "pages": []}

    monkeypatch.setattr(recipe_execution, "run_project", fake_run_project)

    first = await recipe_execution.run_recipe_target(
        str(project),
        "microbiome.permanova",
    )
    assert first["executed_recipes"] == [
        "microbiome.inventory",
        "microbiome.beta_diversity",
        "microbiome.permanova",
    ]
    assert calls[0]["run_data"] is True

    second = await recipe_execution.run_recipe_target(
        str(project),
        "microbiome.permanova",
    )
    assert second["executed_recipes"] == []
    assert second["cache_hits"] == [
        "microbiome.inventory",
        "microbiome.beta_diversity",
        "microbiome.permanova",
    ]
    assert len(calls) == 1

    config_path = project / "code" / "study_config.yml"
    config = yaml.safe_load(config_path.read_text())
    config["analyses"]["recipe_parameters"]["microbiome.permanova"]["permutations"] = 499
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    third = await recipe_execution.run_recipe_target(
        str(project),
        "microbiome.permanova",
    )
    assert third["executed_recipes"] == ["microbiome.permanova"]
    assert third["cache_hits"] == [
        "microbiome.inventory",
        "microbiome.beta_diversity",
    ]
    assert calls[-1]["target_pages"] == ["beta/permanova.qmd"]
    assert calls[-1]["run_data"] is False
