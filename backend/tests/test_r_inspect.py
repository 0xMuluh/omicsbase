"""Tests for bounded R inspection used by the workspace agent."""

from __future__ import annotations

import shutil

import pytest

from app.services.r_inspect import guard_r_code, run_r_inspect

R_AVAILABLE = shutil.which("Rscript") is not None


def test_guard_rejects_network_and_install():
    assert guard_r_code("download.file('http://x', 'y')")
    assert guard_r_code("install.packages('ggplot2')")
    assert guard_r_code("httr::GET('http://example.com')")
    assert guard_r_code("system('ls')")
    assert guard_r_code("saveRDS(x, 'a.rds')")


def test_guard_allows_read_oriented_code():
    assert guard_r_code("data.frame(a = 1:3)") is None
    assert guard_r_code("library(phyloseq); data(GlobalPatterns)") is None


def test_guard_rejects_empty_and_oversized():
    assert guard_r_code("   ")
    assert guard_r_code("x <- 1\n" * 5000)


def test_run_r_inspect_blocks_before_subprocess():
    result = run_r_inspect("download.file('http://x', destfile='y')")
    assert result["status"] == "error"
    assert "download.file" in result["error"]


@pytest.mark.skipif(not R_AVAILABLE, reason="Rscript not available")
def test_run_r_inspect_summarizes_data_frame(monkeypatch):
    monkeypatch.setenv("ALLOW_UNSANDBOXED_EXECUTION", "true")
    result = run_r_inspect("data.frame(a = 1:3, b = letters[1:3])")
    assert result["status"] == "ok", result
    summary = result["summary"]
    assert isinstance(summary, dict)
    assert summary["rows"] == 3
    assert summary["columns"] == 2
    assert summary["colnames"] == ["a", "b"]
    assert len(summary["head"]) == 3
