"""Tests for optional ReportPack staging (explicit id only; null = from-scratch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.services.report_pack import ReportPackError
from app.services.spawner import (
    format_report_pack_catalog_for_llm,
    list_report_packs,
    report_pack_catalog,
    resolve_report_pack,
    spawn_report_pack,
)


def _catalog_pack(domain: str, pack_id: str):
    pack = resolve_report_pack(pack_id, domain=domain)
    assert pack is not None
    return pack


def test_catalog_contains_declared_domain_packs():
    catalog = report_pack_catalog()
    assert "microbiome-diversity" in catalog
    assert "prenatal-metabolomics" in catalog
    assert catalog["microbiome-diversity"].root.exists()
    assert catalog["prenatal-metabolomics"].root.exists()


def test_resolve_report_pack_null_means_no_pack():
    assert resolve_report_pack(None, domain="microbiome") is None
    assert resolve_report_pack("", domain="microbiome") is None
    assert resolve_report_pack("  ", domain="metabolomics") is None


def test_resolve_report_pack_explicit_id_still_works():
    pack = resolve_report_pack("microbiome-diversity", domain="microbiome")
    assert pack is not None
    assert pack.pack_id == "microbiome-diversity"


def test_spawn_copies_verbatim(tmp_path: Path):
    pack = _catalog_pack("microbiome", "microbiome-diversity")
    spawned = spawn_report_pack(str(tmp_path), pack)
    root = pack.root
    for relative, content in spawned.items():
        exemplar = root / relative
        assert exemplar.exists(), f"spawned file not in catalog pack: {relative}"
        assert content == exemplar.read_text(errors="replace")
        target = tmp_path / relative
        assert target.exists()
        assert target.read_text(errors="replace") == content


def test_spawn_includes_data_construction_machinery(tmp_path: Path):
    pack = _catalog_pack("microbiome", "microbiome-diversity")
    spawned = spawn_report_pack(str(tmp_path), pack)
    assert "importMetaPhlAn" in spawned["code/data.R"]
    assert "assign_timepoint" in spawned["code/funct.R"] or "assign_meal" in spawned["code/funct.R"]
    assert "code/main.R" in spawned
    assert "code/_quarto.yml" in spawned


def test_spawn_overwrites_scaffold_placeholders(tmp_path: Path):
    pack = _catalog_pack("microbiome", "microbiome-diversity")
    target = tmp_path / "code/alpha/alpha.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("scaffold placeholder")
    spawned = spawn_report_pack(str(tmp_path), pack)
    assert spawned["code/alpha/alpha.qmd"] != "scaffold placeholder"


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


def test_catalog_listing_for_the_agent_includes_example_paths():
    text = format_report_pack_catalog_for_llm()
    catalog = report_pack_catalog()
    assert "microbiome-diversity" in text
    assert str(catalog["microbiome-diversity"].root) in text
    assert "data container" in text
    assert "copy from" not in text
    assert "parameter form" not in text


def test_catalog_selection_rejects_raw_paths_and_cross_domain_ids(tmp_path: Path):
    with pytest.raises(ReportPackError, match="Unknown ReportPack id"):
        resolve_report_pack(str(tmp_path), domain="microbiome")
    with pytest.raises(ReportPackError, match="not 'microbiome'"):
        resolve_report_pack("prenatal-metabolomics", domain="microbiome")
