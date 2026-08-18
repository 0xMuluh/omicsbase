"""Tests for optional ReportPack staging (explicit id only; null = from-scratch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.schemas.schemas import AnalysisPlan, WorkflowStep

from app.services.spawner import (
    DEFAULT_PACK_IDS,
    EXEMPLAR_ROOTS,
    exemplar_project_files,
    exemplar_report_pack,
    list_report_packs,
    resolve_report_pack,
    spawn_report_pack,
    spawn_exemplar_project,
)
from app.services.report_pack import ReportPackError


def _plan(domain: str, *, report_pack_id: str | None = None) -> AnalysisPlan:
    return AnalysisPlan(
        project_name="Spawn Test",
        domain=domain,
        report_pack_id=report_pack_id,
        study_type="two_group_comparison",
        question="Compare groups",
        grouping_variable="condition",
        group_levels=["A", "B"],
        workflow=[WorkflowStep(id="alpha_diversity", name="Alpha diversity", classification="standard")],
    )


def test_exemplar_roots_resolve_inside_app():
    assert EXEMPLAR_ROOTS["microbiome"].exists()
    assert EXEMPLAR_ROOTS["metabolomics"].exists()


def test_exemplar_report_packs_are_declared():
    assert exemplar_report_pack("microbiome").pack_id == "microbiome-diversity"
    assert exemplar_report_pack("metabolomics").pack_id == "prenatal-metabolomics"
    assert exemplar_report_pack("unknown") is None


def test_resolve_report_pack_null_means_no_pack():
    assert resolve_report_pack(None, domain="microbiome") is None
    assert resolve_report_pack("", domain="microbiome") is None
    assert resolve_report_pack("  ", domain="metabolomics") is None


def test_resolve_report_pack_explicit_id_still_works():
    pack = resolve_report_pack(DEFAULT_PACK_IDS["microbiome"], domain="microbiome")
    assert pack is not None
    assert pack.pack_id == "microbiome-diversity"


def test_exemplar_project_files_cover_the_full_tree():
    files = exemplar_project_files("microbiome")
    names = [path.relative_to(EXEMPLAR_ROOTS["microbiome"]).as_posix() for path in files]
    assert "code/data.R" in names
    assert "code/funct.R" in names
    assert "code/main.R" in names
    assert "code/_quarto.yml" in names
    assert "code/alpha/alpha.qmd" in names
    assert "code/daa/daa_interest.qmd" in names
    assert "README.md" in names
    assert "preprocessing/remove_columns_metaphlan_db_meta4_combined_reports.R" in names

    metabolomics = exemplar_project_files("metabolomics")
    metabolomics_names = [
        path.relative_to(EXEMPLAR_ROOTS["metabolomics"]).as_posix()
        for path in metabolomics
    ]
    assert "README.md" in metabolomics_names
    assert "code/shared/methods_common.R" in metabolomics_names


def test_spawn_copies_verbatim(tmp_path: Path):
    spawned = spawn_exemplar_project(
        str(tmp_path),
        _plan("microbiome", report_pack_id="microbiome-diversity"),
    )
    root = EXEMPLAR_ROOTS["microbiome"]
    for relative, content in spawned.items():
        exemplar = root / relative  # relative already includes code/
        assert exemplar.exists(), f"spawned file not in exemplar: {relative}"
        assert content == exemplar.read_text(errors="replace")
        target = tmp_path / relative
        assert target.exists()
        assert target.read_text(errors="replace") == content


def test_spawn_includes_data_construction_machinery(tmp_path: Path):
    spawned = spawn_exemplar_project(
        str(tmp_path),
        _plan("microbiome", report_pack_id="microbiome-diversity"),
    )
    assert "importMetaPhlAn" in spawned["code/data.R"]
    assert "assign_timepoint" in spawned["code/funct.R"] or "assign_meal" in spawned["code/funct.R"]
    assert "code/main.R" in spawned
    assert "code/_quarto.yml" in spawned


def test_spawn_overwrites_scaffold_placeholders(tmp_path: Path):
    target = tmp_path / "code/alpha/alpha.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("scaffold placeholder")
    spawned = spawn_exemplar_project(
        str(tmp_path),
        _plan("microbiome", report_pack_id="microbiome-diversity"),
    )
    assert spawned["code/alpha/alpha.qmd"] != "scaffold placeholder"


def test_null_report_pack_spawns_nothing(tmp_path: Path):
    plan = _plan("microbiome", report_pack_id=None)
    assert spawn_exemplar_project(str(tmp_path), plan) == {}
    assert list(tmp_path.iterdir()) == []


def test_unknown_domain_spawns_nothing(tmp_path: Path):
    plan = _plan("unknown")
    assert spawn_exemplar_project(str(tmp_path), plan) == {}


def test_custom_catalog_pack_is_selected_by_id_and_copied(tmp_path: Path, monkeypatch):
    catalog_root = tmp_path / "catalog"
    pack_root = catalog_root / "team_report"
    code = pack_root / "code"
    code.mkdir(parents=True)
    (code / "main.R").write_text("quarto::quarto_render()\n")
    (code / "report.qmd").write_text("---\ntitle: Team report\n---\n\nReport.\n")
    (code / "_quarto.yml").write_text(
        "project:\n  type: website\n  output-dir: ../output\n  render:\n    - report.qmd\n"
    )
    (pack_root / "README.md").write_text("Team-authored methods pack.\n")
    (pack_root / "omicsbase-pack.yaml").write_text(
        """
schema_version: "1.0"
id: team-microbiome
version: "2026.1"
domain: microbiome
name: Team microbiome report
entrypoint: code/main.R
default_adaptation: inspect
execution:
  working_directory: code
  render: entrypoint
  artifacts:
    - output/index.html
  steps: []
file_rules:
  - id: main
    match: code/main.R
    role: orchestrator
    adaptation: inspect
  - id: quarto
    match: code/_quarto.yml
    role: assembly
    adaptation: inspect
  - id: pages
    match: code/**/*.qmd
    role: page
    adaptation: inspect
  - id: readme
    match: README.md
    role: static
    adaptation: none
"""
    )
    monkeypatch.setattr(settings, "report_packs_dir", str(catalog_root))

    pack = resolve_report_pack("team-microbiome", domain="microbiome")
    spawned = spawn_report_pack(str(tmp_path / "project"), pack)
    public = next(item for item in list_report_packs() if item["id"] == "team-microbiome")

    assert pack.root == pack_root.resolve()
    assert spawned["code/report.qmd"].startswith("---")
    assert spawned["README.md"] == "Team-authored methods pack.\n"
    assert "root" not in public


def test_catalog_selection_rejects_raw_paths_and_cross_domain_ids(tmp_path: Path):
    with pytest.raises(ReportPackError, match="Unknown ReportPack id"):
        resolve_report_pack(str(tmp_path), domain="microbiome")
    with pytest.raises(ReportPackError, match="not 'microbiome'"):
        resolve_report_pack("prenatal-metabolomics", domain="microbiome")
