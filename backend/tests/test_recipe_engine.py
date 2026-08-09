"""Tests for typed deterministic recipe materialization."""

import json
from pathlib import Path

import pytest
import yaml

from app.schemas.schemas import AnalysisPlan, WorkflowStep
from app.services import generator, qa_gate
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


@pytest.mark.asyncio
async def test_supported_recipe_generation_only_uses_llm_for_pack_adaptation(
    tmp_path: Path,
    monkeypatch,
):
    adapted_targets: list[str] = []

    async def record_adapt_call(**kwargs):
        user_prompt = kwargs.get("user_prompt", "")
        target_marker = "The current file `"
        target = user_prompt.split(target_marker, 1)[1].split("`", 1)[0]
        adapted_targets.append(target)
        # Adapt requests are targeted SEARCH/REPLACE edits, not file rewrites.
        assert "SEARCH/REPLACE edits" in user_prompt
        if target == "code/data.R":
            return json.dumps(
                [
                    {
                        "search": (
                            'metaphlan.file <- '
                            '"../data/metaphlan/latest_metaphlan_db_meta4_combined_reports.txt"'
                        ),
                        "replace": 'metaphlan.file <- "../data/counts.csv"',
                    }
                ]
            )
        if target.startswith("preprocessing/"):
            return json.dumps(
                [
                    {
                        "search": (
                            '"../data/metaphlan/'
                            'metaphlan_db_meta4_combined_reports.txt"'
                        ),
                        "replace": '"../data/counts.csv"',
                    }
                ]
            )
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "Inspection found no study-specific fields requiring adaptation.",
                "evidence": [
                    "The inspected source contains no copied cohort labels, paths, or study columns."
                ],
            }
        )

    monkeypatch.setattr(generator, "call_llm", record_adapt_call)
    monkeypatch.setattr(qa_gate, "run_qa", lambda **_kwargs: qa_gate.QaResult())
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
    # The exemplar project is the report: data machinery + template sections.
    for page in (
        "code/data.R",
        "code/funct.R",
        "code/main.R",
        "code/_quarto.yml",
        "code/alpha/alpha.qmd",
        "code/beta/beta.qmd",
        "code/ratio/ratio.qmd",
        "code/daa/daa_interest.qmd",
        "code/design/study_overview.qmd",
        "code/index.qmd",
    ):
        assert page in generated_names
    # The invented engine layer is gone: no config-driven data contract.
    assert "code/study_config.yml" not in generated_names
    assert "code/recipe_runtime.R" not in generated_names
    # The required loader changed surgically; study-independent machinery is
    # preserved and never sent to the provider.
    assert "importMetaPhlAn" in (tmp_path / "code" / "data.R").read_text()
    assert 'metaphlan.file <- "../data/counts.csv"' in (tmp_path / "code" / "data.R").read_text()
    assert "assign_meal" in (tmp_path / "code" / "funct.R").read_text()
    # The LLM was used only for the adapt (edit) stage.
    assert len(adapted_targets) >= 8
    assert "code/main.R" in adapted_targets
    assert "README.md" not in adapted_targets
    adaptation = json.loads((tmp_path / "adaptation_manifest.json").read_text())
    by_path = {item["path"]: item for item in adaptation["files"]}
    assert by_path["code/data.R"]["status"] == "adapted"
    assert by_path["code/funct.R"]["status"] == "inspected_no_change"
    assert by_path["code/main.R"]["status"] == "inspected_no_change"
    assert by_path["README.md"]["status"] == "copied"


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


def test_apply_edits_bounded_and_similarity_gate():
    from app.services import generator as gen

    template = "\n".join(f"machinery line {i}" for i in range(50))
    whole = template
    # Tiny search + giant replace must be rejected (replace > 60% of file).
    giant = "x" * int(len(template) * 0.9)
    updated = gen._apply_edits_to_file(whole, [{"search": "machinery line 0", "replace": giant}], "data.R")
    assert updated == whole  # rejected: template verbatim

    # A bounded edit applies.
    updated = gen._apply_edits_to_file(
        whole,
        [{"search": "machinery line 5", "replace": "machinery line five"}],
        "data.R",
    )
    assert updated != whole
    assert "machinery line five" in updated
    assert "machinery line 5" not in updated

    # Similarity is a real measure: identical -> 1.0; rewrite -> below threshold.
    assert gen._template_similarity(template, template) == 1.0
    assert gen._template_similarity(template, "totally different content\n" * 10) < gen.TEMPLATE_ADAPT_MIN_SIMILARITY
