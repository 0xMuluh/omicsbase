"""Tests for fuzzy SEARCH/REPLACE apply and file locks."""

from __future__ import annotations

from app.services.apply_edits import (
    apply_search_replace,
    find_similar_lines,
    is_path_locked,
    save_locks,
)


def test_exact_search_replace():
    whole = "alpha <- shannon\nbeta <- bray\n"
    result = apply_search_replace(whole, "alpha <- shannon", "alpha <- simpson", path="code/a.R")
    assert result.ok
    assert result.strategy == "exact"
    assert "simpson" in (result.after or "")
    assert result.to_dict()["diff"]


def test_whitespace_tolerant_replace():
    whole = "  mean(x)\n  sd(x)\n"
    result = apply_search_replace(whole, "mean(x)\nsd(x)\n", "median(x)\nsd(x)\n", path="code/a.R")
    assert result.ok
    assert result.strategy in {"whitespace", "exact"}
    assert "median(x)" in (result.after or "")


def test_failed_replace_includes_hint():
    whole = "group <- metadata$Condition\n"
    result = apply_search_replace(
        whole,
        "group <- metadata$condition",
        "group <- metadata$Group",
        path="code/data.R",
    )
    assert not result.ok
    assert find_similar_lines("group <- metadata$condition", whole)
    assert result.hint


def test_file_locks(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    save_locks(project_dir, ["code/data.R", "input"])
    assert is_path_locked(project_dir, "code/data.R")
    assert is_path_locked(project_dir, "input/counts.csv")
    assert not is_path_locked(project_dir, "code/alpha.qmd")
