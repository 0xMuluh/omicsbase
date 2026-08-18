"""Tests for typed deterministic recipe materialization."""

from pathlib import Path

import pytest
import yaml

from app.schemas.schemas import AnalysisPlan, WorkflowStep
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
    assert (tmp_path / "code" / "alpha" / "alpha.qmd").exists()
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



def test_identifier_overlap_beats_id_name_heuristic(tmp_path: Path):
    """A metadata 'id' column that re-encodes sample IDs must lose to a
    'sample' column whose values actually match the feature table columns."""
    from app.services.recipe_engine import materialize_recipe_project

    counts = tmp_path / "counts.txt"
    counts.write_text("clade_name\tAE1332\tAE2332\tAH1343\n"
                      "s__A\t10\t8\t12\n"
                      "s__B\t1\t2\t0\n")
    metadata = tmp_path / "metadata.csv"
    metadata.write_text("sample,id,condition\n"
                        "AE1332,332AE,case\n"
                        "AE2332,233AE,case\n"
                        "AH1343,343AH,control\n")
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
            {"name": "counts.txt", "role": "feature_table", "columns": ["clade_name", "AE1332", "AE2332", "AH1343"]},
            {"name": "metadata.csv", "role": "metadata", "columns": ["sample", "id", "condition"]},
        ],
        "identifier_candidates": [
            {"file": "metadata.csv", "column": "id", "role": "metadata"},
            {"file": "metadata.csv", "column": "sample", "role": "metadata"},
        ],
    }
    result = materialize_recipe_project(
        str(tmp_path),
        plan,
        manifest,
        {"feature_table": [str(counts)], "metadata": [str(metadata)]},
    )
    config = yaml.safe_load((tmp_path / "code" / "study_config.yml").read_text())
    assert config["identifiers"]["sample_id"] == "sample"
    assert config["identifiers"]["subject_id"] == "sample"


