"""Cancellation behavior for the shared hardened subprocess runner."""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.services import runner


@pytest.mark.asyncio
async def test_run_command_honors_cooperative_cancel_check(tmp_path):
    checks = 0

    def cancel_check():
        nonlocal checks
        checks += 1
        return checks >= 2

    success, output = await runner._run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=str(tmp_path),
        timeout=15,
        cancel_check=cancel_check,
        _bypass_docker=True,
    )

    assert success is False
    assert output == "Process cancelled"
    assert checks >= 2

