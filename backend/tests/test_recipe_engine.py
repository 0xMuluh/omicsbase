"""Tests for typed deterministic recipe materialization."""

from pathlib import Path

import pytest
import yaml

from app.schemas.schemas import AnalysisPlan, WorkflowStep
from app.services import generator
from app.services.recipe_engine import materialize_recipe_project
from app.services.recipe_registry import load_recipe_registry, resolve_recipe


def _plan(domain: str, step: WorkflowStep, grouping: str = "condition") -> AnalysisPlan:
    return AnalysisPlan(
        project_name="Recipe Test",
        domain=domain,
        study_type="two_group_comparison",
        question="Compare groups",
        grouping_variable=grouping,
        group_levels=["A", "B"],
        workflow=[step],
    )


def test_recipe_registry_resolves_domain_specific_aliases():
    registry = load_recipe_registry()

    assert registry["version"] == "1.2.0"
    assert resolve_recipe("alpha_diversity", "microbiome")["id"] == "microbiome.alpha_diversity"
    assert resolve_recipe("permanova", "microbiome")["id"] == "microbiome.permanova"
    assert resolve_recipe("limrots", "microbiome")["id"] == "microbiome.limrots_differential_abundance"
    assert resolve_recipe("linear_feature_scan", "metabolomics")["id"] == "metabolomics.linear_feature_scan"
    assert resolve_recipe("lmm", "metabolomics")["id"] == "metabolomics.repeated_measures_mixed_model"
    assert resolve_recipe("alpha_diversity", "metabolomics") is None


def test_materializes_microbiome_vertical_slice(tmp_path: Path):
    plan = _plan(
        "microbiome",
        WorkflowStep(
            id="alpha_diversity",
            name="Alpha diversity",
            classification="standard",
            recipe_id="microbiome.alpha_diversity",
        ),
    )
    plan.covariates = ["age"]
    plan.workflow[0].parameters = {"metrics": ["observed", "shannon"]}
    manifest = {
        "version": "1.0",
        "domain": "microbiome",
        "files": [
            {"name": "counts.csv", "role": "feature_table", "columns": ["taxon", "S1", "S2"]},
            {"name": "metadata.csv", "role": "metadata", "columns": ["sample_id", "condition", "age"]},
        ],
        "identifier_candidates": [
            {"file": "metadata.csv", "column": "sample_id", "role": "metadata"}
        ],
    }

    result = materialize_recipe_project(
        str(tmp_path),
        plan,
        manifest,
        {
            "feature_table": ["/uploads/counts.csv"],
            "metadata": ["/uploads/metadata.csv"],
        },
    )

    assert result["recipe_ids"] == ["microbiome.inventory", "microbiome.alpha_diversity"]
    assert (tmp_path / "code" / "data.R").exists()
    assert (tmp_path / "code" / "primary" / "alpha_diversity.qmd").exists()
    assert (tmp_path / "code" / "data" / "data_summary.qmd").exists()
    config = yaml.safe_load((tmp_path / "code" / "study_config.yml").read_text())
    assert config["study"]["domain"] == "microbiome"
    assert config["identifiers"]["sample_id"] == "sample_id"
    assert config["variables"]["covariates"] == ["age"]
    assert config["analyses"]["metrics"] == ["observed", "shannon"]
    assert config["analyses"]["recipe_parameters"]["microbiome.alpha_diversity"]["metrics"] == [
        "observed",
        "shannon",
    ]

    plan.workflow[0].enabled = False
    materialize_recipe_project(
        str(tmp_path),
        plan,
        manifest,
        {
            "feature_table": ["/uploads/counts.csv"],
            "metadata": ["/uploads/metadata.csv"],
        },
    )
    assert not (tmp_path / "code" / "primary" / "alpha_diversity.qmd").exists()


def test_materializes_metabolomics_vertical_slice(tmp_path: Path):
    plan = _plan(
        "metabolomics",
        WorkflowStep(
            id="linear_feature_scan",
            name="Metabolite panel",
            classification="standard",
            recipe_id="metabolomics.linear_feature_scan",
        ),
    )
    manifest = {
        "version": "1.0",
        "domain": "metabolomics",
        "files": [
            {
                "name": "metabolites.xlsx",
                "role": "other",
                "columns": ["StudyID", "condition", "metabolite_A", "metabolite_B"],
            }
        ],
        "identifier_candidates": [
            {"file": "metabolites.xlsx", "column": "StudyID", "role": "other"}
        ],
    }

    result = materialize_recipe_project(
        str(tmp_path),
        plan,
        manifest,
        {"other": ["/uploads/metabolites.xlsx"]},
    )

    assert result["recipe_ids"] == [
        "metabolomics.inventory",
        "metabolomics.linear_feature_scan",
    ]
    assert (tmp_path / "code" / "primary" / "metabolite_panel.qmd").exists()
    config = yaml.safe_load((tmp_path / "code" / "study_config.yml").read_text())
    assert config["paths"]["metadata"] == "../data/metabolites.xlsx"


@pytest.mark.asyncio
async def test_supported_recipe_generation_does_not_call_llm(tmp_path: Path, monkeypatch):
    async def fail_if_called(**kwargs):
        raise AssertionError(f"LLM should not be called for a fully supported recipe: {kwargs}")

    monkeypatch.setattr(generator, "call_llm", fail_if_called)
    plan = _plan(
        "microbiome",
        WorkflowStep(
            id="alpha_diversity",
            name="Alpha diversity",
            classification="standard",
            recipe_id="microbiome.alpha_diversity",
        ),
    )
    manifest = {
        "version": "1.0",
        "domain": "microbiome",
        "files": [
            {"name": "counts.csv", "role": "feature_table", "columns": ["taxon", "S1", "S2"]},
            {"name": "metadata.csv", "role": "metadata", "columns": ["sample_id", "condition"]},
        ],
        "identifier_candidates": [
            {"file": "metadata.csv", "column": "sample_id", "role": "metadata"}
        ],
    }

    generated = await generator.generate_project(
        str(tmp_path),
        plan,
        file_summaries=[],
        uploaded_file_paths={
            "feature_table": ["/uploads/counts.csv"],
            "metadata": ["/uploads/metadata.csv"],
        },
        study_manifest=manifest,
    )

    generated_names = {Path(path).relative_to(tmp_path).as_posix() for path in generated}
    assert "code/data.R" in generated_names
    assert "code/primary/alpha_diversity.qmd" in generated_names
    assert "code/design/study_overview.qmd" in generated_names
    assert "code/_quarto.yml" in generated_names
    quarto_config = yaml.safe_load((tmp_path / "code" / "_quarto.yml").read_text())
    assert quarto_config["project"]["render"] == [
        "index.qmd",
        "design/study_overview.qmd",
        "design/analysis_plan.qmd",
        "primary/alpha_diversity.qmd",
        "data/data_summary.qmd",
    ]
    assert quarto_config["website"]["navbar"]["left"] == [
        {"text": "Home", "file": "index.qmd"},
        {
            "text": "Setup & Design",
            "menu": [
                {"text": "Study overview", "file": "design/study_overview.qmd"},
                {"text": "Analysis plan", "file": "design/analysis_plan.qmd"},
            ],
        },
        {
            "text": "Primary Analysis",
            "menu": [
                {"text": "Alpha diversity", "file": "primary/alpha_diversity.qmd"},
            ],
        },
        {
            "text": "Data",
            "menu": [
                {"text": "Data summary", "file": "data/data_summary.qmd"},
            ],
        },
    ]
