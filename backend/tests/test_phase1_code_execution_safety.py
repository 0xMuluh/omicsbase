"""Phase 1: Code Execution Safety tests (Red/Green verification)."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import runner


def test_red_docker_sandbox_defaults_to_true():
    """Verify that use_docker_sandbox is True by default in Settings."""
    assert settings.use_docker_sandbox is True, (
        "CRITICAL: use_docker_sandbox defaults to False in current code, allowing unsandboxed code execution."
    )


def test_red_runner_docker_flags():
    """Verify that Docker sandbox flags include network isolation and resource limits."""
    abs_cwd = "/tmp/test_project"
    cmd = ["Rscript", "-e", "1+1"]

    with patch.object(settings, "use_docker_sandbox", True), \
         patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:

        mock_proc = MagicMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=[b"", b""])
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        import asyncio
        asyncio.run(runner._run_command(cmd, abs_cwd))

        assert mock_exec.called
        args, kwargs = mock_exec.call_args
        docker_cmd = list(args)

        # Check required hardening flags
        assert "--network" in docker_cmd and "none" in docker_cmd[docker_cmd.index("--network") + 1], (
            f"Docker sandbox command missing '--network none': {docker_cmd}"
        )
        assert "--memory" in docker_cmd or any(a.startswith("--memory=") for a in docker_cmd), (
            f"Docker sandbox command missing memory limit: {docker_cmd}"
        )
        assert "--cpus" in docker_cmd or any(a.startswith("--cpus=") for a in docker_cmd), (
            f"Docker sandbox command missing CPU limit: {docker_cmd}"
        )
        assert "--pids-limit" in docker_cmd or any(a.startswith("--pids-limit=") for a in docker_cmd), (
            f"Docker sandbox command missing pids-limit: {docker_cmd}"
        )
        assert "--user" in docker_cmd or any(a.startswith("--user=") for a in docker_cmd), (
            f"Docker sandbox command missing non-root --user: {docker_cmd}"
        )


def test_red_run_chunk_uses_hardened_runner():
    """Verify that /run-chunk does NOT call raw host subprocess.run directly."""
    from app.api import files

    source_code = inspect.getsource(files.run_code_chunk)

    # In current vulnerable code, files.py uses subprocess.run directly
    assert "subprocess.run" not in source_code, (
        "CRITICAL: /run-chunk endpoint directly calls raw host subprocess.run instead of hardened runner!"
    )
