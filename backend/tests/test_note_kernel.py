"""Persistent R kernel: script rendering, request protocol, and lifecycle."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.services import note_kernel
from app.services.note_kernel import (
    KernelDied,
    KernelTimeout,
    build_kernel_script,
    ensure_kernel,
    kill_kernel,
    request_cell,
    shutdown_kernel,
)

pytestmark = pytest.mark.skipif(
    shutil.which("Rscript") is None,
    reason="Rscript not available on the test host",
)


@pytest.fixture
def scope(tmp_path):
    (tmp_path / ".omicsbase").mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


def test_kernel_script_parses():
    script = build_kernel_script(quiet_package_startup=True, capture_plots=True)
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", f"invisible(parse(text = {json.dumps(script)})); cat('ok')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_cells_share_in_memory_workspace(scope):
    kernel = ensure_kernel(scope)
    try:
        request_cell(
            kernel,
            request_id="cell-1",
            run_dir_rel=".omicsbase/note-executions/cell-1",
            source="df <- data.frame(x = 1:3); total <- sum(df$x)",
            timeout_seconds=60,
        )
        request_cell(
            kernel,
            request_id="cell-2",
            run_dir_rel=".omicsbase/note-executions/cell-2",
            source='cat("total =", total)',
            timeout_seconds=60,
        )
    finally:
        kill_kernel(kernel)

    console = (Path(scope) / ".omicsbase" / "note-executions" / "cell-2" / ".note_console.txt").read_text()
    assert "total = 6" in console

    timing = json.loads(
        (Path(scope) / ".omicsbase" / "note-executions" / "cell-2" / "timing.json").read_text()
    )
    assert timing["save_seconds"] == 0
    assert timing["eval_seconds"] >= 0


def test_kernel_reuses_live_process(scope):
    first = ensure_kernel(scope)
    second = ensure_kernel(scope)
    try:
        assert first.pid == second.pid
    finally:
        kill_kernel(first)


def test_shutdown_saves_workspace(scope):
    kernel = ensure_kernel(scope)
    request_cell(
        kernel,
        request_id="seed",
        run_dir_rel=".omicsbase/note-executions/seed",
        source="persisted_value <- 42",
        timeout_seconds=60,
    )
    shutdown_kernel(kernel)
    workspace = Path(scope) / ".omicsbase" / "note-kernel" / "workspace.RData"
    assert workspace.exists()

    # A fresh kernel loads the saved workspace.
    fresh = ensure_kernel(scope)
    try:
        request_cell(
            fresh,
            request_id="read-back",
            run_dir_rel=".omicsbase/note-executions/read-back",
            source='cat("persisted =", persisted_value)',
            timeout_seconds=60,
        )
    finally:
        kill_kernel(fresh)
    console = (Path(scope) / ".omicsbase" / "note-executions" / "read-back" / ".note_console.txt").read_text()
    assert "persisted = 42" in console


def test_dead_kernel_raises_kernel_died(scope):
    kernel = ensure_kernel(scope)
    kill_kernel(kernel)
    with pytest.raises(KernelDied):
        request_cell(
            kernel,
            request_id="nope",
            run_dir_rel=".omicsbase/note-executions/nope",
            source="1 + 1",
            timeout_seconds=5,
        )


def test_timeout_kills_kernel(scope):
    kernel = ensure_kernel(scope)
    with pytest.raises(KernelTimeout):
        request_cell(
            kernel,
            request_id="slow",
            run_dir_rel=".omicsbase/note-executions/slow",
            source="Sys.sleep(30)",
            timeout_seconds=2,
        )
    assert not note_kernel._pid_alive(kernel.pid)
