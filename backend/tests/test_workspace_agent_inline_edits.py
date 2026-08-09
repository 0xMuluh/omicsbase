"""Tests for inline SEARCH/REPLACE edit execution in workspace_agent.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.workspace_agent import _execute_inline_edit_project


def test_execute_inline_edit_success(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    code_dir = project_dir / "code"
    code_dir.mkdir()
    file_path = code_dir / "index.qmd"
    file_path.write_text("library(ggplot2)\nggplot(df, aes(x=a)) + theme_bw()\n")

    project = SimpleNamespace(project_dir=str(project_dir))
    arguments = {
        "path": "code/index.qmd",
        "search": "theme_bw()",
        "replace": "theme_minimal()",
    }

    res = _execute_inline_edit_project(project, arguments)
    assert res["status"] == "ok"
    assert "theme_minimal()" in file_path.read_text()


def test_execute_inline_edit_patch_envelope_without_outer_path(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    code_dir = project_dir / "code"
    code_dir.mkdir()
    file_path = code_dir / "index.qmd"
    file_path.write_text("theme_bw()\n")
    project = SimpleNamespace(project_dir=str(project_dir))
    patch = """*** Begin Patch
*** Update File: code/index.qmd
@@
-theme_bw()
+theme_minimal()
*** End Patch"""

    res = _execute_inline_edit_project(project, {"patch": patch})

    assert res["status"] == "ok"
    assert file_path.read_text() == "theme_minimal()\n"


def test_execute_inline_edit_failure(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    code_dir = project_dir / "code"
    code_dir.mkdir()
    file_path = code_dir / "index.qmd"
    file_path.write_text("library(ggplot2)\nggplot(df, aes(x=a)) + theme_bw()\n")

    project = SimpleNamespace(project_dir=str(project_dir))
    arguments = {
        "path": "code/index.qmd",
        "search": "NONEXISTENT_SNIPPET",
        "replace": "theme_minimal()",
    }

    res = _execute_inline_edit_project(project, arguments)
    assert res["status"] == "error"
    assert "SEARCH block failed" in res["error"]


@pytest.mark.parametrize("malicious_path", ["../outside.qmd", "code/../../outside.qmd"])
def test_execute_inline_edit_rejects_parent_traversal(tmp_path, malicious_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "code").mkdir()
    outside = tmp_path / "outside.qmd"
    outside.write_text("do not change\n")

    project = SimpleNamespace(project_dir=str(project_dir))
    res = _execute_inline_edit_project(
        project,
        {"path": malicious_path, "search": "do not change", "replace": "changed"},
    )

    assert res["status"] == "error"
    assert "outside the project" in res["error"]
    assert outside.read_text() == "do not change\n"


def test_execute_inline_edit_rejects_absolute_path(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    outside = tmp_path / "outside.qmd"
    outside.write_text("do not change\n")

    project = SimpleNamespace(project_dir=str(project_dir))
    res = _execute_inline_edit_project(
        project,
        {"path": str(outside), "search": "do not change", "replace": "changed"},
    )

    assert res["status"] == "error"
    assert "outside the project" in res["error"]
    assert outside.read_text() == "do not change\n"


def test_execute_inline_edit_rejects_symlink_escape(tmp_path):
    project_dir = tmp_path / "proj"
    code_dir = project_dir / "code"
    code_dir.mkdir(parents=True)
    outside = tmp_path / "outside.qmd"
    outside.write_text("do not change\n")
    (code_dir / "escape.qmd").symlink_to(outside)

    project = SimpleNamespace(project_dir=str(project_dir))
    res = _execute_inline_edit_project(
        project,
        {"path": "code/escape.qmd", "search": "do not change", "replace": "changed"},
    )

    assert res["status"] == "error"
    assert "outside the project" in res["error"]
    assert outside.read_text() == "do not change\n"
