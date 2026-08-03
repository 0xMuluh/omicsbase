"""Tests for inline SEARCH/REPLACE edit execution in workspace_agent.py."""

from __future__ import annotations

from types import SimpleNamespace
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
