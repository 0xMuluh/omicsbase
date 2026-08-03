"""Execute deterministic ingestion recipes on small synthetic studies."""

from __future__ import annotations

import asyncio
import csv
import shutil
import subprocess
from pathlib import Path

import pytest

from app.schemas.schemas import AnalysisPlan, WorkflowStep
from app.services import generator
from app.services.recipe_execution import run_recipe_target


R_AVAILABLE = shutil.which("Rscript") is not None
QUARTO_AVAILABLE = shutil.which("quarto") is not None


def _r_dependencies_available() -> bool:
    if not R_AVAILABLE:
        return False
    result = subprocess.run(
        [
            "Rscript",
            "-e",
            'quit(status = if (requireNamespace("yaml", quietly=TRUE) && requireNamespace("jsonlite", quietly=TRUE)) 0 else 1)',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


@pytest.mark.skipif(not _r_dependencies_available(), reason="R recipe dependencies unavailable")
def test_microbiome_ingestion_recipe_executes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_UNSANDBOXED_EXECUTION", "true")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    counts = upload_dir / "counts.csv"
    metadata = upload_dir / "metadata.csv"
    _write_csv(
        counts,
        [
            ["taxon", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"],
            ["TaxonA", 10, 8, 12, 9, 2, 1, 3, 2],
            ["TaxonB", 1, 2, 0, 1, 9, 8, 10, 7],
            ["TaxonC", 3, 1, 4, 2, 2, 4, 1, 3],
        ],
    )
    _write_csv(
        metadata,
        [
            ["sample_id", "condition"],
            ["S1", "Control"],
            ["S2", "Control"],
            ["S3", "Treatment"],
            ["S4", "Control"],
            ["S5", "Treatment"],
            ["S6", "Treatment"],
            ["S7", "Treatment"],
            ["S8", "Treatment"],
        ],
    )
    project = tmp_path / "project"
    _copy_inputs(project, counts, metadata)
    plan = _plan("microbiome", "alpha_diversity", "microbiome.alpha_diversity")
    plan.workflow.extend(
        [
            WorkflowStep(
                id="beta_diversity",
                name="Beta diversity",
                classification="standard",
                recipe_id="microbiome.beta_diversity",
            ),
            WorkflowStep(
                id="permanova",
                name="PERMANOVA",
                classification="standard",
                recipe_id="microbiome.permanova",
            ),
            WorkflowStep(
                id="differential_abundance",
                name="LimROTS",
                classification="contested",
                recipe_id="microbiome.limrots_differential_abundance",
            ),
        ]
    )
    manifest = _manifest("microbiome", counts.name, metadata.name, ["taxon", "S1", "S2"], ["sample_id", "condition"])
    asyncio.run(
        generator.generate_project(
            str(project),
            plan,
            file_summaries=[],
            study_manifest=manifest,
            uploaded_file_paths={
                "feature_table": [str(counts)],
                "metadata": [str(metadata)],
            },
        )
    )

    subprocess.run(["Rscript", "data.R"], cwd=project / "code", check=True)

    assert (project / "output" / "derived" / "analysis_data.rds").exists()
    assert (project / "output" / "derived" / "data_validation.json").exists()
    if QUARTO_AVAILABLE:
        subprocess.run(["quarto", "render"], cwd=project / "code", check=True)
        assert (project / "output" / "primary" / "alpha_diversity.html").exists()
        assert (project / "output" / "design" / "study_overview.html").exists()
        assert (project / "output" / "data" / "data_summary.html").exists()
        assert (project / "output" / "results" / "permanova.csv").exists()
        assert (project / "output" / "results" / "limrots_differential_abundance.csv").exists()
        targeted = asyncio.run(
            run_recipe_target(str(project), "microbiome.alpha_diversity")
        )
        cached = asyncio.run(
            run_recipe_target(str(project), "microbiome.alpha_diversity")
        )
        assert targeted["status"] == "completed"
        assert cached["executed_recipes"] == []
        assert "microbiome.alpha_diversity" in cached["cache_hits"]


@pytest.mark.skipif(not _r_dependencies_available(), reason="R recipe dependencies unavailable")
def test_metabolomics_ingestion_recipe_executes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_UNSANDBOXED_EXECUTION", "true")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    metabolites = upload_dir / "serum_metabolites.csv"
    rows = [["StudyID", "visit", "condition", "metabolite_A", "metabolite_B"]]
    for subject in range(1, 21):
        for visit in (1, 2):
            rows.append(
                [
                    f"S{subject}",
                    visit,
                    "Control" if subject <= 10 else "Treatment",
                    subject + visit * 0.5,
                    subject * 2.0 + visit,
                ]
            )
    _write_csv(metabolites, rows)
    project = tmp_path / "project"
    _copy_inputs(project, metabolites)
    plan = _plan("metabolomics", "linear_feature_scan", "metabolomics.linear_feature_scan")
    plan.workflow.append(
        WorkflowStep(
            id="repeated_measures_mixed_model",
            name="Longitudinal models",
            classification="standard",
            recipe_id="metabolomics.repeated_measures_mixed_model",
        )
    )
    manifest = _manifest(
        "metabolomics",
        metabolites.name,
        metabolites.name,
        ["StudyID", "visit", "condition", "metabolite_A", "metabolite_B"],
        ["StudyID", "visit", "condition", "metabolite_A", "metabolite_B"],
        feature_role="other",
    )
    asyncio.run(
        generator.generate_project(
            str(project),
            plan,
            file_summaries=[],
            study_manifest=manifest,
            uploaded_file_paths={"other": [str(metabolites)]},
        )
    )

    subprocess.run(["Rscript", "data.R"], cwd=project / "code", check=True)

    assert (project / "output" / "derived" / "analysis_data.rds").exists()
    assert (project / "output" / "derived" / "data_validation.json").exists()
    assert (project / "output" / "derived" / "MAE.rds").exists()
    if QUARTO_AVAILABLE:
        subprocess.run(["quarto", "render"], cwd=project / "code", check=True)
        assert (project / "output" / "primary" / "metabolite_panel.html").exists()
        assert (project / "output" / "results" / "metabolite_lmm.csv").exists()


def _plan(domain: str, step_id: str, recipe_id: str) -> AnalysisPlan:
    return AnalysisPlan(
        project_name="R smoke test",
        domain=domain,
        study_type="two_group_comparison",
        question="Compare conditions",
        grouping_variable="condition",
        group_levels=["Control", "Treatment"],
        workflow=[
            WorkflowStep(
                id=step_id,
                name=step_id,
                classification="standard",
                recipe_id=recipe_id,
            )
        ],
    )


def _manifest(
    domain: str,
    feature_name: str,
    metadata_name: str,
    feature_columns: list[str],
    metadata_columns: list[str],
    *,
    feature_role: str = "feature_table",
) -> dict:
    files = [
        {"name": feature_name, "role": feature_role, "columns": feature_columns},
    ]
    if metadata_name != feature_name:
        files.append({"name": metadata_name, "role": "metadata", "columns": metadata_columns})
    return {
        "version": "1.0",
        "domain": domain,
        "files": files,
        "identifier_candidates": [
            {"file": metadata_name, "column": metadata_columns[0], "role": "metadata"}
        ],
    }


def _copy_inputs(project: Path, *paths: Path) -> None:
    data_dir = project / "data"
    data_dir.mkdir(parents=True)
    for path in paths:
        shutil.copy2(path, data_dir / path.name)


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
