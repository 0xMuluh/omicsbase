"""Tests for the presentation gate (structural + language enforcement)."""

from __future__ import annotations

from pathlib import Path

from app.services import qa_gate


def _write(project: Path, relative: str, content: str) -> Path:
    target = project / "code" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_zero_byte_page_is_structural_violation(tmp_path: Path):
    _write(tmp_path, "daa/daa_interest.qmd", "")
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert "daa/daa_interest.qmd" in result.structural


def test_unfilled_shell_is_structural_violation(tmp_path: Path):
    shell = "---\ntitle: X\n---\n\n## Heading\n\n<!-- FILL: develop this section -->\n"
    _write(tmp_path, "ratio/ratio.qmd", shell)
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert "ratio/ratio.qmd" in result.structural


def test_meta_narration_is_language_finding(tmp_path: Path):
    page = ("---\ntitle: X\n---\n\n## Results\n\n" + ("This page loads the normalized matrices and compares them. " * 10))
    _write(tmp_path, "data/normalization_consensus.qmd", page)
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert not result.structural
    assert any("normalization_consensus" in line and "narration" in line for line in result.language)


def test_filler_language_is_language_finding(tmp_path: Path):
    page = ("---\ntitle: X\n---\n\n## Results\n\n" + ("This comprehensive analysis provides valuable insights. " * 10))
    _write(tmp_path, "alpha/alpha.qmd", page)
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert any("filler" in line for line in result.language)


def test_copied_template_study_terms_are_findings(tmp_path: Path):
    page = ("---\ntitle: X\n---\n\n## Results\n\n" + ("The prenatal cohort analysis is described below. " * 10))
    _write(tmp_path, "design/study_overview.qmd", page)
    result = qa_gate.run_qa(str(tmp_path), project_name="OGB")
    assert any("prenatal" in line for line in result.language)


def test_exemplar_comment_notes_are_ignored(tmp_path: Path):
    page = ("---\ntitle: X\n---\n\n<!-- Exemplar: templates/metabolomics/prenatal_diet_metabolomics/code/design/study_overview.qmd -->\n\n"
            + ("Study results are summarized in the tables below. " * 10))
    _write(tmp_path, "design/study_overview.qmd", page)
    result = qa_gate.run_qa(str(tmp_path), project_name="OGB")
    assert not result.structural
    assert not result.language


def test_clean_page_passes_gate(tmp_path: Path):
    page = ("---\ntitle: Alpha diversity\nformat:\n  html:\n    code-fold: true\n---\n\n"
            "## Group-wise comparisons\n\n"
            + ("Shannon and observed richness are compared across the diet groups. " * 10) + "\n")
    _write(tmp_path, "alpha/alpha.qmd", page)
    result = qa_gate.run_qa(str(tmp_path), project_name="OGB")
    assert result.passed


def test_nav_missing_file_is_error(tmp_path: Path):
    _write(tmp_path, "alpha/alpha.qmd", "---\ntitle: X\n---\n\n" + ("Content. " * 100))
    (tmp_path / "code" / "_quarto.yml").write_text("project:\n  render:\n    - alpha/alpha.qmd\n    - missing.qmd\n")
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert any("missing.qmd" in line for line in result.errors)


def test_render_globs_and_exclusions_are_not_literal_missing_files(tmp_path: Path):
    _write(tmp_path, "alpha/alpha.qmd", "---\ntitle: X\n---\n\n" + ("Content. " * 100))
    (tmp_path / "code" / "_quarto.yml").write_text(
        "project:\n"
        "  render:\n"
        "    - '*.qmd'\n"
        "    - alpha/*.qmd\n"
        "    - '!alpha/draft.qmd'\n"
    )

    result = qa_gate.run_qa(str(tmp_path), project_name="Test")

    assert not result.errors


def test_prune_files_removes_only_requested(tmp_path: Path):
    keep = _write(tmp_path, "alpha/alpha.qmd", "---\ntitle: X\n---\n\n" + ("Content. " * 100))
    drop = _write(tmp_path, "daa/daa_mtc.qmd", "")
    removed = qa_gate.prune_files(str(tmp_path), ["daa/daa_mtc.qmd"])
    assert removed == ["daa/daa_mtc.qmd"]
    assert not drop.exists()
    assert keep.exists()


def test_source_lint_catches_known_failure_patterns(tmp_path: Path):
    source = """
library(haven)
imported <- read_sav("study.sav")
top <- imported %>% slice_head(n = min(dplyr::n(), 5))
value <- if_else("sample_id" %in% names(imported), imported$sample_id, NA)
long <- imported %>% pivot_longer(cols = everything())
label <- as.numeric(haven_labelled_column)
first <- unique(imported$group)[[1]]
joined <- bind_cols(clinical[, c("id")], omics[, c("id")])
"""
    _write(tmp_path, "analysis.R", source)

    findings = qa_gate.lint_source_files(tmp_path)

    for rule in (
        "unsafe_dplyr_n",
        "if_else_missing_column",
        "mixed_pivot_longer",
        "haven_labelled_numeric",
        "unchecked_unique_index",
        "independent_bind_cols",
    ):
        assert any(rule in finding for finding in findings), (rule, findings)
    result = qa_gate.run_qa(str(tmp_path), project_name="Test")
    assert result.errors == findings


def test_source_lint_allows_explicit_safe_variants(tmp_path: Path):
    source = """
safe_top <- imported %>% slice_head(n = 5)
value <- if ("sample_id" %in% names(imported)) imported$sample_id else NA
long <- imported %>% pivot_longer(
    cols = everything(),
    values_transform = list(value = as.numeric)
)
joined <- bind_cols(clinical, omics)
first <- if (length(unique(imported$group)) > 0) unique(imported$group)[[1]] else NA_character_
"""
    _write(tmp_path, "safe.R", source)

    assert qa_gate.lint_source_files(tmp_path) == []
