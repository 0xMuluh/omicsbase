"""Tests for strict, open-world report-pack classification."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.services.capability_contract import (
    resolve_plan_capabilities,
    validate_capability_bindings,
)
from app.services.report_pack import ReportPackError, load_report_pack
from app.services.spawner import EXEMPLAR_ROOTS, exemplar_project_files


def test_declared_exemplar_packs_classify_every_spawned_file():
    microbiome = load_report_pack(EXEMPLAR_ROOTS["microbiome"], domain="microbiome")
    metabolomics = load_report_pack(EXEMPLAR_ROOTS["metabolomics"], domain="metabolomics")

    assert microbiome.source == "declared"
    assert microbiome.execution is not None
    assert [step.path for step in microbiome.execution.steps] == [
        "preprocessing/remove_columns_metaphlan_db_meta4_combined_reports.R",
        "code/data.R",
        "code/data_delta.R",
    ]
    assert microbiome.execution.artifacts == ("output/index.html",)
    assert microbiome.classify("code/data.R").adaptation == "required"
    assert microbiome.classify("code/main.R").adaptation == "inspect"
    alpha = microbiome.classify("code/alpha/alpha.qmd")
    assert alpha.role == "page"
    assert alpha.adaptation == "inspect"
    assert alpha.matched_rule_id == "report-pages"

    assert metabolomics.classify("code/make_mae.R").adaptation == "required"
    assert metabolomics.execution is not None
    assert [step.role for step in metabolomics.execution.steps] == [
        "data_loader",
        "analysis",
        "validator",
    ]
    assert metabolomics.classify("code/report_utils.R").adaptation == "required"
    assert metabolomics.classify("code/validate_results.R").adaptation == "required"
    assert metabolomics.classify("code/shared/methods_common.R").adaptation == "none"
    assert metabolomics.classify("code/toc-toggle.css").adaptation == "none"

    for domain, pack in (("microbiome", microbiome), ("metabolomics", metabolomics)):
        spawned = [
            path.relative_to(EXEMPLAR_ROOTS[domain]).as_posix()
            for path in exemplar_project_files(domain)
        ]
        inventory = pack.resolved_inventory(spawned)
        assert len(inventory) == len(spawned)
        assert {item["path"] for item in inventory} == set(spawned)
        assert all(item["adaptation"] in {"none", "inspect", "required"} for item in inventory)


def test_metabolomics_exemplar_artifact_paths_are_consistent():
    root = EXEMPLAR_ROOTS["metabolomics"]
    code = root / "code"
    loader = (code / "make_mae.R").read_text()
    analysis = (code / "prenatal_diet_analysis.R").read_text()
    validator = (code / "validate_results.R").read_text()
    qmd_sources = [path.read_text() for path in sorted(code.rglob("*.qmd"))]

    mae_artifact = "../data/MAE_original.rds"
    primary_results_artifact = (
        "../output/results/targeted/prenatal_diet_study2_results.rds"
    )

    # The loader creates MAE_original.rds; analysis and validation consume it.
    assert re.search(r'output_prefix\s*=\s*"MAE"', loader)
    assert f'mae_path <- "{mae_artifact}"' in analysis
    assert f'mae_path <- "{mae_artifact}"' in validator

    # The analysis producer, validator, and every primary-results page agree.
    assert 'output_dir <- "../output/results/targeted"' in analysis
    assert 'file.path(output_dir, "prenatal_diet_study2_results.rds")' in analysis
    assert f'rds_path <- "{primary_results_artifact}"' in validator

    primary_page_refs = {
        match.group(1)
        for source in qmd_sources
        for match in re.finditer(
            r'["\'](\.\./[^"\']*prenatal_diet_study2_results[^"\']*\.rds)["\']',
            source,
        )
    }
    mae_page_refs = {
        match.group(1)
        for source in qmd_sources
        for match in re.finditer(r'["\'](\.\./[^"\']*MAE[^"\']*\.rds)["\']', source)
    }
    assert primary_page_refs == {primary_results_artifact}
    assert mae_page_refs == {mae_artifact}

    quarto_config = yaml.safe_load((code / "_quarto.yml").read_text())
    rendered_pages = set(quarto_config["project"]["render"])

    def navigation_files(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "file":
                    yield nested
                else:
                    yield from navigation_files(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from navigation_files(nested)

    linked_pages = set(navigation_files(quarto_config["website"]["navbar"]))
    secondary_figures = "secondary/secondary_figures.qmd"
    assert secondary_figures not in rendered_pages
    assert secondary_figures not in linked_pages
    for page in rendered_pages:
        assert "prenatal_diet_secondary_results.rds" not in (code / page).read_text()

    all_source = "\n".join([loader, analysis, validator, *qmd_sources])
    for legacy_name in ("MAE2", "data_v2", "targeted_v2", "study2_results_v2"):
        assert legacy_name not in all_source


def test_builtin_packs_have_executable_capability_contracts():
    """Keep the shipped scientific packs honest as their manifests evolve."""
    templates_root = Path(__file__).parents[2] / "templates"
    manifest_paths = sorted(templates_root.glob("*/**/omicsbase-pack.yaml"))
    assert manifest_paths, "at least one built-in ReportPack is required"

    for manifest_path in manifest_paths:
        pack = load_report_pack(manifest_path.parent)
        assert pack.execution is not None
        assert pack.capabilities

        step_ids = {step.step_id for step in pack.execution.steps}
        for capability in pack.capabilities:
            assert capability.execution_steps
            assert set(capability.execution_steps) <= step_ids
            for relative in (*capability.sources, *capability.validators):
                assert (pack.root / relative).is_file(), (
                    f"{pack.pack_id}: missing capability file {relative}"
                )
            for relative in capability.outputs:
                assert not Path(relative).is_absolute()

        contract = resolve_plan_capabilities(
            pack, {"capabilities": [item.capability_id for item in pack.capabilities]}
        )
        validation = validate_capability_bindings(pack.root, contract)
        assert validation.valid, (
            f"{pack.pack_id}: capability validators are invalid: "
            f"{[issue.as_dict() for issue in validation.issues]}"
        )


def test_arbitrary_directory_fallback_is_conservative(tmp_path: Path):
    (tmp_path / "code").mkdir()
    pack = load_report_pack(tmp_path, domain="unknown")

    assert pack.source == "discovered"
    assert pack.classify("code/data.R").adaptation == "required"
    assert pack.classify("code/custom.R").adaptation == "inspect"
    assert pack.classify("code/report.qmd").adaptation == "inspect"
    assert pack.classify("code/notes.txt").adaptation == "inspect"
    assert pack.classify("code/theme.css").adaptation == "none"


def test_file_rules_are_first_match(tmp_path: Path):
    (tmp_path / "code" / "design").mkdir(parents=True)
    (tmp_path / "code" / "main.R").write_text("invisible(NULL)\n")
    (tmp_path / "omicsbase-pack.yaml").write_text(
        """
schema_version: "1.0"
id: precedence
version: "1"
domain: test
entrypoint: code/main.R
default_adaptation: inspect
file_rules:
  - id: all-pages
    match: code/**/*.qmd
    role: page
    adaptation: inspect
  - id: design-pages
    match: code/design/*.qmd
    role: page
    adaptation: required
"""
    )

    classified = load_report_pack(tmp_path).classify("code/design/plan.qmd")

    assert classified.matched_rule_id == "all-pages"
    assert classified.adaptation == "inspect"


@pytest.mark.parametrize(
    "manifest,match",
    [
        (
            """
schema_version: "2"
id: bad
version: "1"
domain: test
default_adaptation: inspect
""",
            "Unsupported report-pack schema",
        ),
        (
            """
schema_version: "1.0"
id: bad
version: "1"
domain: test
default_adaptation: inspect
unexpected: true
""",
            "Unknown manifest field",
        ),
        (
            """
schema_version: "1.0"
id: bad
version: "1"
domain: test
default_adaptation: inspect
file_rules:
  - id: escape
    match: ../outside.R
    role: analysis
    adaptation: inspect
""",
            "safe relative path",
        ),
        (
            """
schema_version: "1.0"
id: bad
version: "1"
domain: test
default_adaptation: sometimes
""",
            "default_adaptation",
        ),
    ],
)
def test_invalid_manifests_fail_closed(tmp_path: Path, manifest: str, match: str):
    (tmp_path / "omicsbase-pack.yaml").write_text(manifest)

    with pytest.raises(ReportPackError, match=match):
        load_report_pack(tmp_path)


def test_resolved_inventory_records_rule_and_pack_hash():
    pack = load_report_pack(EXEMPLAR_ROOTS["microbiome"], domain="microbiome")

    inventory = pack.resolved_inventory(["code/data.R", "code/main.R"])

    assert pack.manifest_sha256
    assert pack.source_tree_sha256
    assert inventory == [
        {
            "path": "code/data.R",
            "role": "data_loader",
            "adaptation": "required",
            "study_dependent": True,
            "matched_rule_id": "primary-loader",
            "classification_source": "declared",
        },
        {
            "path": "code/main.R",
            "role": "orchestrator",
            "adaptation": "inspect",
            "study_dependent": True,
            "matched_rule_id": "main-orchestrator",
            "classification_source": "declared",
        },
    ]


def test_declared_prompt_references_exist_under_skills_root():
    skills_root = Path(__file__).resolve().parents[2] / "skills"
    for domain, root in EXEMPLAR_ROOTS.items():
        pack = load_report_pack(root, domain=domain)
        assert pack.prompt_references
        for reference in pack.prompt_references:
            assert (skills_root / reference).is_file(), reference


def test_source_tree_digest_changes_with_source_bytes(tmp_path: Path):
    (tmp_path / "code").mkdir()
    source = tmp_path / "code" / "report.qmd"
    source.write_text("# First\n")
    first = load_report_pack(tmp_path, domain="unknown").source_tree_sha256

    source.write_text("# Second\n")
    second = load_report_pack(tmp_path, domain="unknown").source_tree_sha256

    assert first != second


def test_source_inventory_rejects_symlinks(tmp_path: Path):
    outside = tmp_path.parent / "outside-report-pack.txt"
    outside.write_text("secret")
    (tmp_path / "escape.qmd").symlink_to(outside)

    with pytest.raises(ReportPackError, match="may not be a symlink"):
        load_report_pack(tmp_path, domain="unknown")


def test_execution_schema_requires_artifact_and_matching_roles(tmp_path: Path):
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "main.R").write_text("invisible(NULL)\n")
    (tmp_path / "code" / "analyze.R").write_text("invisible(NULL)\n")
    (tmp_path / "omicsbase-pack.yaml").write_text(
        """
schema_version: "1.0"
id: execution-test
version: "1"
domain: test
entrypoint: code/main.R
default_adaptation: inspect
execution:
  working_directory: code
  render: entrypoint
  artifacts: []
  steps:
    - id: analyze
      path: code/analyze.R
      role: analysis
file_rules:
  - id: main
    match: code/main.R
    role: orchestrator
    adaptation: inspect
  - id: analysis
    match: code/analyze.R
    role: analysis
    adaptation: required
"""
    )

    with pytest.raises(ReportPackError, match="artifacts must be a non-empty"):
        load_report_pack(tmp_path)

    manifest = (tmp_path / "omicsbase-pack.yaml").read_text().replace(
        "artifacts: []",
        "artifacts:\n    - output/index.html",
    ).replace("role: analysis\nfile_rules:", "role: data_loader\nfile_rules:")
    (tmp_path / "omicsbase-pack.yaml").write_text(manifest)

    with pytest.raises(ReportPackError, match="declares role.*classification"):
        load_report_pack(tmp_path)
