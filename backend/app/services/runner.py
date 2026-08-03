"""Runner service — executes generated analysis projects incrementally."""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Callable

import yaml

import shutil
from app.config import settings

logger = logging.getLogger(__name__)

MAX_SUBPROCESS_OUTPUT_CHARS = 10_000_000
SUBPROCESS_TRUNCATION_MARKER = "\n[output truncated]"


async def run_project(
    project_dir: str,
    progress_callback: Callable[[str, str, str], None] | None = None,
    start_page: str | None = None,
    run_data: bool | None = None,
    target_pages: list[str] | None = None,
) -> dict:
    """Execute the analysis project incrementally.

    The old path ran main.R, which rendered the whole Quarto website every time.
    This path renders data prep only when stale, then renders one QMD page at a
    time so failures preserve generated source and partial preview output.
    """
    project_path = Path(project_dir)
    code_dir = project_path / "code"
    output_dir = project_path / "output"
    result = {"status": "completed", "logs": [], "errors": [], "pages": []}

    def _report(step_id: str, status: str, line: str = ""):
        if progress_callback:
            progress_callback(step_id, status, line)
        if line:
            result["logs"].append(line)

    if not code_dir.exists():
        message = f"Generated code directory not found: {code_dir}"
        _report("setup", "failed", message)
        result["status"] = "failed"
        result["errors"].append({"step": "setup", "error": message})
        return result

    all_pages = _load_quarto_pages(code_dir)
    if target_pages is not None:
        requested = set(target_pages)
        pages = [
            page
            for page in all_pages
            if page in requested or Path(page).name in requested
        ]
    else:
        pages = _pages_from_start(all_pages, start_page) if start_page else all_pages
    if not pages:
        message = "No QMD files were found to render."
        _report("quarto_pages", "failed", message)
        result["status"] = "failed"
        result["errors"].append({"step": "quarto_pages", "error": message})
        return result

    _write_incremental_index(
        output_dir,
        all_pages,
        completed_pages=_existing_rendered_pages(output_dir, all_pages),
    )

    data_r = code_dir / "data.R"
    if data_r.exists():
        should_run_data = _should_run_data(project_path, data_r) if run_data is None else run_data
        if should_run_data:
            _report("data_r_exec", "running", "Running data.R...")
            success, output = await _run_command(
                ["Rscript", "data.R"],
                cwd=str(code_dir),
                progress_callback=lambda line: _report("data_r_exec", "running", line),
            )
            if not success:
                summary = _extract_error_summary(output)
                _report("data_r_exec", "failed", f"data.R failed: {summary}")
                result["status"] = "failed"
                result["errors"].append({"step": "data_r", "file": "code/data.R", "error": output})
                return result
            _report("data_r_exec", "completed", "data.R completed successfully")
        else:
            _report("data_r_exec", "completed", "data.R cache current")

    completed_pages = _existing_rendered_pages(output_dir, all_pages)

    # Distinguish leaf analysis pages from final assembly pages (e.g. index.qmd)
    leaf_pages = [p for p in pages if Path(p).name.lower() not in {"index.qmd", "summary.qmd"}]
    assembly_pages = [p for p in pages if Path(p).name.lower() in {"index.qmd", "summary.qmd"}]

    semaphore = asyncio.Semaphore(3)

    async def _render_single(page: str) -> bool:
        async with semaphore:
            step_id = _page_step_id(page)
            page_path = code_dir / page
            output_file = _html_path_for_page(output_dir, page)

            if not page_path.exists():
                message = f"QMD file is missing: code/{page}"
                _report(step_id, "failed", message)
                result["status"] = "failed"
                result["failed_page"] = page
                result["errors"].append({"step": "qmd", "file": f"code/{page}", "error": message})
                _write_incremental_index(output_dir, all_pages, completed_pages, failed_page=page)
                return False

            _report(step_id, "running", f"Rendering {page}")
            success, output = await _run_command(
                ["quarto", "render", page],
                cwd=str(code_dir),
                progress_callback=lambda line: _report(step_id, "running", line),
                timeout=_timeout_for_page(page),
            )
            if not success:
                summary = _extract_error_summary(output)
                _report(step_id, "failed", f"{page} failed: {summary}")
                result["status"] = "failed"
                result["failed_page"] = page
                result["pages"].append({"file": f"code/{page}", "status": "failed"})
                result["errors"].append(
                    {
                        "step": "qmd",
                        "file": f"code/{page}",
                        "page": page,
                        "error": output,
                        "timeout": "timed out" in output.lower(),
                    }
                )
                _write_incremental_index(output_dir, all_pages, completed_pages, failed_page=page)
                return False

            if page not in completed_pages:
                completed_pages.append(page)
            result["pages"].append({"file": f"code/{page}", "status": "completed", "output": str(output_file)})
            _write_incremental_index(output_dir, all_pages, completed_pages)
            _report(step_id, "completed", f"Rendered {page}")
            return True

    # Render leaf analysis pages concurrently
    if leaf_pages:
        for batch_task in asyncio.as_completed([_render_single(p) for p in leaf_pages]):
            ok = await batch_task
            if not ok:
                return result

    # Once leaf analysis pages complete, render assembly page(s)
    for page in assembly_pages:
        ok = await _render_single(page)
        if not ok:
            return result

    index_html = output_dir / "index.html"
    if index_html.exists():
        _report("verify", "completed", f"Output verified: {index_html}")
    else:
        _report("verify", "warning", "Warning: index.html not found in output directory")

    return result


def _load_quarto_pages(code_dir: Path) -> list[str]:
    """Load the ordered QMD render list from _quarto.yml, with a safe fallback."""
    quarto_yml = code_dir / "_quarto.yml"
    if quarto_yml.exists():
        try:
            config = yaml.safe_load(quarto_yml.read_text(encoding="utf-8")) or {}
            render_entries = (config.get("project") or {}).get("render") or []
            pages = [_normalise_render_entry(entry) for entry in render_entries]
            pages = [page for page in pages if page and page.endswith(".qmd")]
            if pages:
                return pages
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", quarto_yml, exc)

    return sorted(path.name for path in code_dir.glob("*.qmd"))


def _normalise_render_entry(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("file", "input", "path"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
    return None


def _pages_from_start(pages: list[str], start_page: str) -> list[str]:
    """Return pages from start_page onward, matching basename or relative path."""
    wanted = Path(start_page).name
    for index, page in enumerate(pages):
        if page == start_page or Path(page).name == wanted:
            return pages[index:]
    return pages


def _should_run_data(project_dir: Path, data_r: Path) -> bool:
    """Return True when data.R or uploaded data is newer than the derived cache."""
    cache = project_dir / "output" / "derived" / "analysis_data.rds"
    if not cache.exists():
        return True

    cache_mtime = cache.stat().st_mtime
    tracked_inputs = [data_r]
    data_dir = project_dir / "data"
    if data_dir.exists():
        tracked_inputs.extend(path for path in data_dir.rglob("*") if path.is_file())

    return any(path.exists() and path.stat().st_mtime > cache_mtime for path in tracked_inputs)


def _existing_rendered_pages(output_dir: Path, pages: list[str]) -> list[str]:
    return [page for page in pages if _html_path_for_page(output_dir, page).exists()]


def _html_path_for_page(output_dir: Path, page: str) -> Path:
    page_path = Path(page)
    return output_dir / page_path.with_suffix(".html")


def _page_step_id(page: str) -> str:
    stem = Path(page).with_suffix("").as_posix()
    safe = "".join(char if char.isalnum() else "_" for char in stem).strip("_")
    return f"qmd_{safe}"


def _extract_error_summary(output: str) -> str:
    """Prefer the actionable error line over Quarto's traceback tail."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "Command failed without output"

    priority_prefixes = ("Error:", "Error in ", "! ", "Quitting from")
    for line in reversed(lines):
        if line.startswith(priority_prefixes):
            return line[:500]

    return lines[-1][:500]


def _write_incremental_index(
    output_dir: Path,
    pages: list[str],
    completed_pages: list[str],
    failed_page: str | None = None,
) -> None:
    """Write a minimal preview shell even when only part of the report rendered."""
    output_dir.mkdir(parents=True, exist_ok=True)
    completed_set = set(completed_pages)
    first_page = next((page for page in pages if page in completed_set), None)
    iframe_src = html.escape(str(_html_path_for_page(output_dir, first_page).relative_to(output_dir))) if first_page else ""

    if iframe_src:
        preview = f'<iframe name="preview" src="{iframe_src}" title="Report preview"></iframe>'
    else:
        preview = """
        <section class="empty">
          <h2>Preview is waiting for the first rendered page</h2>
          <p>Generated source is still available while the report is rendering.</p>
        </section>
        """

    status_note = ""
    if failed_page:
        status_note = f'<div class="status failed">Stopped at {html.escape(Path(failed_page).stem.replace("_", " ").title())}. Earlier rendered pages remain available.</div>'
    elif first_page and len(completed_pages) < len(pages):
        status_note = '<div class="status pending">Rendering in progress. The first completed page is shown below.</div>'

    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OmicsBase Report</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #050706; color: #f6f6f3; }}
    body {{ margin: 0; min-height: 100vh; background: #050706; }}
    .layout {{ min-height: 100vh; position: relative; }}
    .status {{ position: absolute; z-index: 5; top: 16px; left: 16px; max-width: min(560px, calc(100vw - 32px)); padding: 10px 14px; border-radius: 999px; font-size: 13px; backdrop-filter: blur(10px); }}
    .status.pending {{ background: rgba(15, 23, 42, 0.82); color: #cbd5e1; border: 1px solid rgba(255,255,255,.08); }}
    .status.failed {{ background: rgba(127, 29, 29, 0.9); color: #fecaca; border: 1px solid rgba(248,113,113,.28); }}
    h2 {{ margin: 0 0 8px; font-size: 22px; }}
    p {{ color: #9ca3af; line-height: 1.5; margin: 0; }}
    main {{ min-width: 0; background: #fff; min-height: 100vh; }}
    iframe {{ width: 100%; height: 100vh; border: 0; background: #fff; }}
    .empty {{ display: grid; place-content: center; min-height: 100vh; text-align: center; background: #050706; }}
  </style>
</head>
<body>
  <div class="layout">
    {status_note}
    <main>{preview}</main>
  </div>
</body>
</html>
"""
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")


import os
import signal
import concurrent.futures

async def _run_command(
    cmd: list[str],
    cwd: str,
    progress_callback: Callable[[str], None] | None = None,
    timeout: int = 1800,
    cancel_check: Callable[[], bool] | None = None,
    _bypass_docker: bool = False,
) -> tuple[bool, str]:
    """Run a command in a hardened Docker sandbox container or subprocess, streaming output.

    ``timeout`` is a wall-clock budget for the whole command.
    Returns (success: bool, output: str).
    """
    exec_cmd = list(cmd)
    exec_cwd = cwd

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
            abs_cwd = str(Path(cwd).resolve())
            exec_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", getattr(settings, "docker_memory_limit", "2g"),
                "--cpus", getattr(settings, "docker_cpu_limit", "2.0"),
                "--pids-limit", str(getattr(settings, "docker_pids_limit", 100)),
                "--user", "1000:1000",
                "--security-opt", "no-new-privileges:true",
                "-v", f"{abs_cwd}:/workspace",
                "-w", "/workspace",
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
            return await _run_command(cmd, cwd, progress_callback=progress_callback, timeout=timeout, cancel_check=cancel_check, _bypass_docker=True)

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
    """Synchronous wrapper for _run_command, ensuring hardened execution boundary across all sync callers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(_run_command(cmd, cwd, timeout=timeout))).result()
    else:
        return asyncio.run(_run_command(cmd, cwd, timeout=timeout))


def _timeout_for_page(page: str) -> int:
    """Allow heavy association / model pages a longer wall-clock budget."""
    name = Path(page).name.lower()
    heavy_markers = (
        "association",
        "primary",
        "sensitivity",
        "multiple_testing",
        "limrots",
        "permanova",
        "mixed",
        "lmm",
        "model",
    )
    if any(marker in name for marker in heavy_markers):
        return 7200
    return 1800


async def check_prerequisites() -> dict[str, bool]:
    """Check that R and Quarto are available.

    SECURITY NOTE (Gap 4.1 Audit Finding):
    These version probes execute Rscript and quarto directly on the host system without
    Docker sandboxing. This blast radius is explicitly ACCEPTED because:
    1. The commands are hardcoded constant argument vectors ('Rscript --version', 'quarto --version').
    2. No user inputs, tenant parameters, or file paths are passed into these subprocess invocations.
    """
    checks = {}

    try:
        proc = await asyncio.create_subprocess_exec(
            "Rscript", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        checks["r_available"] = proc.returncode == 0
        checks["r_version"] = output.decode().strip()
    except FileNotFoundError:
        checks["r_available"] = False

    try:
        proc = await asyncio.create_subprocess_exec(
            "quarto", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        checks["quarto_available"] = proc.returncode == 0
        checks["quarto_version"] = output.decode().strip()
    except FileNotFoundError:
        checks["quarto_available"] = False

    return checks
