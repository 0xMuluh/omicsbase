"""Runner service — executes generated analysis projects incrementally."""

from __future__ import annotations

import asyncio
import fcntl
import html
import json
import logging
import os
from pathlib import Path
from typing import Callable
from contextlib import asynccontextmanager

import yaml

import shutil
from app.config import settings
from app.services.execution_contract import (
    ExecutionContractError,
    load_execution_contract,
)
from app.services.capability_contract import (
    CapabilityContractError,
    load_capability_contract,
)

logger = logging.getLogger(__name__)

MAX_SUBPROCESS_OUTPUT_CHARS = 10_000_000
SUBPROCESS_TRUNCATION_MARKER = "\n[output truncated]"


@asynccontextmanager
async def _project_execution_lock(project_path: Path):
    """Serialize mutating R/Quarto runs across workers for one project."""
    lock_dir = project_path / ".omicsbase"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = (lock_dir / "execution.lock").open("a+")
    try:
        await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        handle.close()


async def run_project(
    project_dir: str,
    progress_callback: Callable[[str, str, str], None] | None = None,
    start_page: str | None = None,
    run_data: bool | None = None,
    target_pages: list[str] | None = None,
    resume_from_step: str | None = None,
) -> dict:
    """Run a project under its cross-process execution lock."""
    project_path = Path(project_dir).resolve()
    async with _project_execution_lock(project_path):
        # Read the advisory file only after acquiring the same lock used by
        # edits. This avoids missing a change written while a run was waiting.
        pending = _read_pending_invalidation(project_path) if resume_from_step is None else None
        effective_resume = resume_from_step or (pending or {}).get("resume_from_step")
        effective_target_pages = target_pages
        if effective_target_pages is None and not effective_resume and pending is not None:
            raw_pages = pending.get("targeted_pages")
            if isinstance(raw_pages, list):
                effective_target_pages = [
                    str(page) for page in raw_pages
                    if isinstance(page, str) and page.strip()
                ] or None
        # A pending page-only invalidation is a render task, not a data task.
        # Preserve explicit run_data choices, but default the advisory path to
        # skipping execution steps so editing a QMD never reruns the workflow.
        effective_run_data = run_data
        if (
            effective_run_data is None
            and pending is not None
            and effective_target_pages
            and not effective_resume
        ):
            effective_run_data = False
        result = await _run_project_unlocked(
            project_dir=str(project_path),
            progress_callback=progress_callback,
            start_page=start_page,
            run_data=effective_run_data,
            target_pages=effective_target_pages,
            resume_from_step=effective_resume,
        )
        # A completed run has consumed the pending source invalidation. Keep it
        # on failures so a later retry resumes from the same safe boundary.
        pending_step = (pending or {}).get("resume_from_step") if pending is not None else None
        consumed_pending = pending is not None and (not pending_step or run_data is not False)
        if result.get("status") == "completed" and consumed_pending:
            (project_path / ".omicsbase" / "invalidation.json").unlink(missing_ok=True)
        return result


def _read_pending_invalidation(project_path: Path) -> dict[str, object] | None:
    """Read advisory edit invalidation metadata without trusting it as code."""

    target = project_path / ".omicsbase" / "invalidation.json"
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    resume = raw.get("resume_from_step")
    if resume is not None and (not isinstance(resume, str) or not resume.strip()):
        return None
    return raw


async def _run_project_unlocked(
    project_dir: str,
    progress_callback: Callable[[str, str, str], None] | None = None,
    start_page: str | None = None,
    run_data: bool | None = None,
    target_pages: list[str] | None = None,
    resume_from_step: str | None = None,
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

    try:
        execution_contract = load_execution_contract(project_path)
    except ExecutionContractError as exc:
        message = f"Invalid ReportPack execution contract: {exc}"
        _report("execution_contract", "failed", message)
        result["status"] = "failed"
        result["errors"].append(
            {"step": "execution_contract", "file": "execution_contract.json", "error": message}
        )
        return result

    capability_path = project_path / ".omicsbase" / "capabilities.json"
    if capability_path.exists():
        try:
            capability_contract = load_capability_contract(project_path)
            if execution_contract is not None:
                known_steps = {step.step_id for step in execution_contract.steps}
                unknown_steps = sorted({
                    step_id
                    for item in capability_contract.selected
                    for step_id in item.capability.execution_steps
                    if step_id not in known_steps
                })
                if unknown_steps:
                    raise CapabilityContractError(
                        "Capability contract references unknown execution step(s): "
                        + ", ".join(unknown_steps)
                    )
        except CapabilityContractError as exc:
            message = f"Invalid capability contract: {exc}"
            _report("capabilities", "failed", message)
            result["status"] = "failed"
            result["errors"].append(
                {"step": "capabilities", "file": ".omicsbase/capabilities.json", "error": message}
            )
            return result

    source_dir = (
        execution_contract.working_path
        if execution_contract is not None
        else code_dir
    )
    source_root = (
        execution_contract.working_directory
        if execution_contract is not None
        else "code"
    )
    if not source_dir.exists():
        message = f"Generated source directory not found: {source_dir}"
        _report("setup", "failed", message)
        result["status"] = "failed"
        result["errors"].append({"step": "setup", "error": message})
        return result

    if execution_contract is not None:
        artifact_baseline = _artifact_snapshot(execution_contract)
        working_path = execution_contract.working_path
        if run_data is not False:
            resume_index = next((index for index, step in enumerate(execution_contract.steps) if step.step_id == resume_from_step), 0) if resume_from_step else 0
            for index, step in enumerate(execution_contract.steps):
                step_id = f"pack_{step.step_id}"
                if resume_from_step and index < resume_index:
                    _report(step_id, "skipped", f"Resuming from {resume_from_step}; {step.path} remains current")
                    continue
                command_path = os.path.relpath(
                    execution_contract.step_path(step),
                    start=working_path,
                )
                _report(step_id, "running", f"Running {step.path} ({step.role})...")
                success, output = await _run_command(
                    ["Rscript", command_path],
                    cwd=str(working_path),
                    progress_callback=lambda line, current=step_id: _report(
                        current, "running", line
                    ),
                    timeout=_timeout_for_execution_role(step.role),
                    sandbox_root=str(project_path),
                )
                if not success:
                    summary = _extract_error_summary(output)
                    _report(step_id, "failed", f"{step.path} failed: {summary}")
                    result["status"] = "failed"
                    result["errors"].append(
                        {
                            "step": step.role,
                            "step_id": step.step_id,
                            "file": step.path,
                            "error": output,
                            "timeout": "timed out" in output.lower(),
                        }
                    )
                    return result
                _report(step_id, "completed", f"Completed {step.path}")
        elif execution_contract.steps:
            _report(
                "pack_steps",
                "completed",
                "ReportPack analysis steps skipped for this render",
            )

        # Entrypoints are the pack's own orchestration surface. Targeted recipe
        # and repair runs retain incremental rendering so a single requested
        # page does not force a complete site rebuild.
        use_entrypoint = (
            execution_contract.render == "entrypoint"
            and target_pages is None
            and start_page is None
        )
        if use_entrypoint:
            entrypoint_path = execution_contract.entrypoint_path
            if entrypoint_path is None:  # guarded by strict contract parsing
                raise AssertionError("Validated entrypoint contract has no entrypoint")
            command_path = os.path.relpath(entrypoint_path, start=working_path)
            _report(
                "pack_entrypoint",
                "running",
                f"Running ReportPack entrypoint {execution_contract.entrypoint}...",
            )
            success, output = await _run_command(
                ["Rscript", command_path],
                cwd=str(working_path),
                progress_callback=lambda line: _report("pack_entrypoint", "running", line),
                timeout=7200,
                sandbox_root=str(project_path),
            )
            if not success:
                summary = _extract_error_summary(output)
                _report(
                    "pack_entrypoint",
                    "failed",
                    f"{execution_contract.entrypoint} failed: {summary}",
                )
                result["status"] = "failed"
                result["errors"].append(
                    {
                        "step": "entrypoint",
                        "file": execution_contract.entrypoint,
                        "error": output,
                        "timeout": "timed out" in output.lower(),
                    }
                )
                return result
            _report(
                "pack_entrypoint",
                "completed",
                f"Completed {execution_contract.entrypoint}",
            )
            artifact_errors = _declared_artifact_errors(
                execution_contract,
                baseline=artifact_baseline,
            )
            if artifact_errors:
                message = "; ".join(artifact_errors)
                _report("verify", "failed", message)
                result["status"] = "failed"
                result["errors"].append(
                    {"step": "artifacts", "error": message}
                )
                return result
            _report(
                "verify",
                "completed",
                "Verified fresh declared report artifact(s): "
                + ", ".join(execution_contract.artifacts),
            )
            return result

    data_r = code_dir / "data.R"
    if execution_contract is None and data_r.exists():
        should_run_data = _should_run_data(project_path, data_r) if run_data is None else run_data
        if should_run_data:
            _report("data_r_exec", "running", "Running data.R...")
            success, output = await _run_command(
                ["Rscript", "data.R"],
                cwd=str(code_dir),
                progress_callback=lambda line: _report("data_r_exec", "running", line),
                sandbox_root=str(project_path),
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

    all_pages = _load_quarto_pages(source_dir)
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

    completed_pages = _existing_rendered_pages(output_dir, all_pages)

    # Distinguish leaf analysis pages from final assembly pages (e.g. index.qmd)
    leaf_pages = [p for p in pages if Path(p).name.lower() not in {"index.qmd", "summary.qmd"}]
    assembly_pages = [p for p in pages if Path(p).name.lower() in {"index.qmd", "summary.qmd"}]

    semaphore = asyncio.Semaphore(3)

    async def _render_single(page: str) -> bool:
        async with semaphore:
            step_id = _page_step_id(page)
            page_path = source_dir / page
            output_file = _html_path_for_page(output_dir, page)
            source_file = f"{source_root}/{page}"

            if not page_path.exists():
                message = f"QMD file is missing: {source_file}"
                _report(step_id, "failed", message)
                result["status"] = "failed"
                result["failed_page"] = page
                result["errors"].append({"step": "qmd", "file": source_file, "error": message})
                _write_incremental_index(output_dir, all_pages, completed_pages, failed_page=page)
                return False

            _report(step_id, "running", f"Rendering {page}")
            success, output = await _run_command(
                ["quarto", "render", page],
                cwd=str(source_dir),
                progress_callback=lambda line: _report(step_id, "running", line),
                timeout=_timeout_for_page(page),
                sandbox_root=str(project_path),
            )
            if not success:
                summary = _extract_error_summary(output)
                _report(step_id, "failed", f"{page} failed: {summary}")
                result["status"] = "failed"
                result["failed_page"] = page
                result["pages"].append({"file": source_file, "status": "failed"})
                result["errors"].append(
                    {
                        "step": "qmd",
                        "file": source_file,
                        "page": page,
                        "error": output,
                        "timeout": "timed out" in output.lower(),
                    }
                )
                _write_incremental_index(output_dir, all_pages, completed_pages, failed_page=page)
                return False

            if page not in completed_pages:
                completed_pages.append(page)
            result["pages"].append({"file": source_file, "status": "completed", "output": str(output_file)})
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

    if execution_contract is not None:
        artifact_errors = _declared_artifact_errors(
            execution_contract,
            baseline=artifact_baseline,
        )
        if artifact_errors:
            message = "; ".join(artifact_errors)
            _report("verify", "failed", message)
            result["status"] = "failed"
            result["errors"].append({"step": "artifacts", "error": message})
            return result
        _report(
            "verify",
            "completed",
            "Verified fresh declared report artifact(s): "
            + ", ".join(execution_contract.artifacts),
        )
    else:
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


def repairs_require_analysis_rerun(
    project_dir: str | Path,
    repaired_paths: list[str],
) -> bool:
    """Decide whether repaired source invalidates the analysis stage.

    Legacy projects key this decision to data.R. A declared ReportPack can
    source any R helper from any execution step, so an R-source repair
    conservatively invalidates its full ordered analysis workflow.
    """
    try:
        contract = load_execution_contract(project_dir)
    except ExecutionContractError:
        return True
    if contract is not None:
        return any(Path(path).suffix.lower() == ".r" for path in repaired_paths)
    return any(Path(path).name.lower() == "data.r" for path in repaired_paths)


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


def _artifact_snapshot(contract) -> dict[str, tuple[int, int] | None]:
    snapshot: dict[str, tuple[int, int] | None] = {}
    for relative in contract.artifacts:
        path = contract.artifact_path(relative)
        if not path.is_file():
            snapshot[relative] = None
            continue
        stat = path.stat()
        snapshot[relative] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _declared_artifact_errors(
    contract,
    *,
    baseline: dict[str, tuple[int, int] | None],
) -> list[str]:
    errors: list[str] = []
    for relative in contract.artifacts:
        path = contract.artifact_path(relative)
        if not path.is_file():
            errors.append(f"Declared artifact was not produced: {relative}")
            continue
        stat = path.stat()
        current = (stat.st_mtime_ns, stat.st_size)
        if baseline.get(relative) is not None and current == baseline.get(relative):
            errors.append(f"Declared artifact was not refreshed by this run: {relative}")
    return errors


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


import signal
import concurrent.futures

async def _run_command(
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
            return await _run_command(
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


def _timeout_for_execution_role(role: str) -> int:
    if role == "analysis":
        return 7200
    if role == "data_loader":
        return 3600
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
