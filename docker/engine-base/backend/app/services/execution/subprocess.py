"""Hardened subprocess execution for analysis and rendering."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import shutil
import signal
from pathlib import Path
from typing import Callable

from app.config import settings

logger = logging.getLogger(__name__)

MAX_SUBPROCESS_OUTPUT_CHARS = 10_000_000
SUBPROCESS_TRUNCATION_MARKER = "\n[output truncated]"

async def run_command(
    cmd: list[str],
    cwd: str,
    progress_callback: Callable[[str], None] | None = None,
    timeout: int = 1800,
    cancel_check: Callable[[], bool] | None = None,
    _bypass_docker: bool = False,
    sandbox_root: str | None = None,
) -> tuple[bool, str]:
    """Run a command in a hardened Docker sandbox container or subprocess, streaming output.

    ``timeout`` is a wall-clock budget for the whole command.
    Returns (success: bool, output: str).
    """
    exec_cmd = list(cmd)
    exec_cwd = cwd
    abs_cwd_path = Path(cwd).resolve()
    mount_root = Path(sandbox_root).resolve() if sandbox_root else abs_cwd_path
    try:
        relative_cwd = abs_cwd_path.relative_to(mount_root)
    except ValueError as exc:
        raise ValueError(
            f"Command cwd {abs_cwd_path} is outside sandbox root {mount_root}"
        ) from exc

    if not settings.use_docker_sandbox and not settings.dev_mode and not _bypass_docker:
        raise RuntimeError(
            "CRITICAL SECURITY ERROR: use_docker_sandbox is disabled but dev_mode is False. "
            "Production execution requires Docker sandboxing. Set DEV_MODE=true only in local development."
        )

    if settings.use_docker_sandbox and not _bypass_docker:
        if not shutil.which("docker"):
            if os.getenv("ALLOW_UNSANDBOXED_EXECUTION", "").lower() != "true":
                raise RuntimeError(
                    "CRITICAL SECURITY ERROR: use_docker_sandbox is enabled, but 'docker' binary was not found on PATH. "
                    "Production execution requires Docker sandboxing. Set ALLOW_UNSANDBOXED_EXECUTION=true only in local development."
                )
            logger.warning("Docker binary missing on PATH; falling back to unsandboxed host execution under ALLOW_UNSANDBOXED_EXECUTION=true override.")
        else:
            container_cwd = Path("/workspace") / relative_cwd
            exec_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", getattr(settings, "docker_memory_limit", "2g"),
                "--cpus", getattr(settings, "docker_cpu_limit", "2.0"),
                "--pids-limit", str(getattr(settings, "docker_pids_limit", 100)),
                "--user", "1000:1000",
                "--security-opt", "no-new-privileges:true",
                "-v", f"{mount_root}:/workspace",
                "-w", container_cwd.as_posix(),
                settings.docker_image,
            ] + cmd
            exec_cwd = None

    logger.info("Running: %s in %s (timeout=%ss)", " ".join(exec_cmd), cwd, timeout)

    try:
        process = await asyncio.create_subprocess_exec(
            *exec_cmd,
            cwd=exec_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=None,
            start_new_session=True,
        )

        output_lines = []
        output_chars = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1, timeout)
        poll_seconds = 0.1
        cancel_poll_seconds = 0.5
        last_cancel_check = loop.time()
        while True:
            if cancel_check and loop.time() - last_cancel_check >= cancel_poll_seconds:
                last_cancel_check = loop.time()
                try:
                    if cancel_check():
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except Exception:
                            process.kill()
                        await process.wait()
                        return False, "Process cancelled"
                except Exception as exc:
                    logger.warning("Cancellation check failed: %s", exc)
            remaining = deadline - loop.time()
            if remaining <= 0:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    process.kill()
                await process.wait()
                return False, "Process timed out"

            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=min(poll_seconds, remaining),
                )
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                continue

            if not line:
                break

            decoded = line.decode("utf-8", errors="replace").rstrip()
            separator_chars = 1 if output_lines else 0
            remaining = MAX_SUBPROCESS_OUTPUT_CHARS - output_chars - separator_chars
            if len(decoded) > max(0, remaining):
                if remaining > 0:
                    output_lines.append(decoded[:remaining])
                output_lines.append(SUBPROCESS_TRUNCATION_MARKER)
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                await process.wait()
                return False, "\n".join(output_lines)
            output_lines.append(decoded)
            output_chars += separator_chars + len(decoded)
            if progress_callback:
                progress_callback(decoded)

        await process.wait()
        full_output = "\n".join(output_lines)
        success = process.returncode == 0

        if not success and os.getenv("ALLOW_UNSANDBOXED_EXECUTION", "").lower() == "true" and "docker" in exec_cmd[0]:
            logger.warning("Docker execution failed (exit %d); falling back to unsandboxed host execution under ALLOW_UNSANDBOXED_EXECUTION=true override.", process.returncode)
            return await run_command(
                cmd,
                cwd,
                progress_callback=progress_callback,
                timeout=timeout,
                cancel_check=cancel_check,
                _bypass_docker=True,
                sandbox_root=sandbox_root,
            )

        if not success:
            logger.error("Command failed (exit %d): %s", process.returncode, full_output[-500:])

        return success, full_output

    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}. Is it installed and on PATH?"
        logger.error(msg)
        return False, msg
    except Exception as e:
        logger.exception("Unexpected error running command")
        return False, str(e)


def run_command_sync(cmd: list[str], cwd: str, timeout: int = 1800) -> tuple[bool, str]:
    """Synchronous wrapper for run_command, ensuring hardened execution boundary across all sync callers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(run_command(cmd, cwd, timeout=timeout))).result()
    else:
        return asyncio.run(run_command(cmd, cwd, timeout=timeout))

