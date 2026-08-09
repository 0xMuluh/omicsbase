"""Tests for runner timeout handling and repair skip logic."""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.services import runner
from app.tasks.analysis import _is_timeout_failure


def test_timeout_for_heavy_association_page_is_extended():
    assert runner._timeout_for_page("primary_association_models.qmd") == 7200
    assert runner._timeout_for_page("descriptive_analysis.qmd") == 1800


def test_is_timeout_failure_detects_timed_out_render():
    assert _is_timeout_failure(
        {
            "errors": [
                {
                    "step": "qmd",
                    "page": "primary_association_models.qmd",
                    "error": "primary_association_models.qmd failed: Process timed out",
                    "timeout": True,
                }
            ]
        }
    )
    assert not _is_timeout_failure(
        {"errors": [{"step": "qmd", "error": "object 'x' not found"}]}
    )


@pytest.mark.asyncio
async def test_run_command_uses_wall_clock_not_idle_stdout(monkeypatch):
    """Silent compute should survive as long as total wall time is within budget."""

    class FakeStdout:
        def __init__(self):
            self.calls = 0

        async def readline(self):
            self.calls += 1
            if self.calls == 1:
                # First read idles until the runner's short poll times out.
                await asyncio.sleep(100)
                return b""
            if self.calls == 2:
                return b"done\n"
            return b""  # EOF so the runner can exit the read loop

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.returncode = None

        def kill(self):
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                self.returncode = 0

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_exec)
    success, output = await runner._run_command(["echo"], cwd=".", timeout=15)
    assert success is True
    assert "done" in output


@pytest.mark.asyncio
async def test_parallel_leaf_rendering(tmp_path, monkeypatch):
    """Leaf QMD pages render concurrently while index.qmd waits for completion."""
    project_dir = tmp_path / "project"
    code_dir = project_dir / "code"
    output_dir = project_dir / "output"
    code_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    (code_dir / "01_alpha.qmd").write_text("# Alpha\n")
    (code_dir / "02_beta.qmd").write_text("# Beta\n")
    (code_dir / "index.qmd").write_text("# Index\n")

    rendered_pages = []

    async def fake_run_command(
        cmd,
        cwd,
        progress_callback=None,
        timeout=1800,
        sandbox_root=None,
    ):
        page = cmd[2]
        rendered_pages.append(page)
        # Create output html file to simulate successful rendering
        html_name = page.replace(".qmd", ".html")
        (output_dir / html_name).write_text("<html>Done</html>")
        return True, "rendered"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    res = await runner.run_project(str(project_dir))
    assert res["status"] == "completed"
    # index.qmd must be rendered LAST after leaf pages complete
    assert rendered_pages[-1] == "index.qmd"
    assert set(rendered_pages[:2]) == {"01_alpha.qmd", "02_beta.qmd"}


@pytest.mark.asyncio
async def test_run_command_caps_accumulated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_SUBPROCESS_OUTPUT_CHARS", 64)

    success, output = await runner._run_command(
        [sys.executable, "-c", "print(\"x\" * 1000)"],
        cwd=str(tmp_path),
        timeout=15,
        _bypass_docker=True,
    )

    assert success is False
    assert runner.SUBPROCESS_TRUNCATION_MARKER in output
