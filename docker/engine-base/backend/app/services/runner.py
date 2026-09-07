"""Runner service — executes generated analysis projects incrementally."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import uuid
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from app.services.execution_provenance import (
    snapshot_execution_inputs,
    write_execution_provenance,
)

from app.config import settings

from app.services.execution.quarto import (
    _artifact_snapshot,
    _declared_artifact_errors,
    _existing_rendered_pages,
    _extract_error_summary,
    _html_path_for_page,
    _load_quarto_pages,
    _page_step_id,
    _pages_from_start,
    _resolve_quarto_source,
    _should_run_data,
    _timeout_for_execution_role,
    _timeout_for_page,
    _write_incremental_index,
    repairs_require_analysis_rerun,
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
    source_dir: str | None = None,
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

        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        input_snapshot = snapshot_execution_inputs(project_path)
        events: list[dict[str, object]] = []

        def _provenance_progress(step_id: str, status: str, line: str = "") -> None:
            events.append({
                "step": step_id,
                "status": status,
                "time": datetime.now(timezone.utc).isoformat(),
                "detail": line,
            })
            if progress_callback:
                progress_callback(step_id, status, line)

        result: dict | None = None
        try:
            result = await _run_project_unlocked(
                project_dir=str(project_path),
                progress_callback=_provenance_progress,
                start_page=start_page,
                run_data=effective_run_data,
                target_pages=effective_target_pages,
                resume_from_step=effective_resume,
                requested_source_dir=source_dir,
                command_runner=_run_command,
            )
            # A completed run has consumed the pending source invalidation. Keep
            # it on failures so a later retry resumes from the same safe boundary.
            pending_step = (pending or {}).get("resume_from_step") if pending is not None else None
            consumed_pending = pending is not None and (not pending_step or run_data is not False)
            if result.get("status") == "completed" and consumed_pending:
                (project_path / ".omicsbase" / "invalidation.json").unlink(missing_ok=True)
            return result
        finally:
            # Provenance is best-effort and must never turn a successful report
            # into a failed job. The runner result still carries the record so
            # task callers can surface validator evidence immediately.
            try:
                provenance = write_execution_provenance(
                    project_path,
                    run_id=run_id,
                    started_at=started_at,
                    result=result,
                    events=events,
                    input_snapshot=input_snapshot,
                    resume_from_step=effective_resume,
                    target_pages=effective_target_pages,
                )
                if result is not None:
                    result["provenance"] = provenance
            except Exception as exc:  # pragma: no cover - filesystem failure is non-fatal
                logger.warning("Could not persist execution provenance for %s: %s", project_path, exc)


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
    requested_source_dir: str | None = None,
    command_runner=None,
) -> dict:
    from app.services.execution.orchestrator import run_project_unlocked

    return await run_project_unlocked(
        project_dir=project_dir,
        progress_callback=progress_callback,
        start_page=start_page,
        run_data=run_data,
        target_pages=target_pages,
        resume_from_step=resume_from_step,
        requested_source_dir=requested_source_dir,
        command_runner=command_runner,
    )


async def _run_command(*args, **kwargs):
    from app.services.execution import subprocess as execute

    execute.MAX_SUBPROCESS_OUTPUT_CHARS = MAX_SUBPROCESS_OUTPUT_CHARS
    execute.SUBPROCESS_TRUNCATION_MARKER = SUBPROCESS_TRUNCATION_MARKER
    return await execute.run_command(*args, **kwargs)


def run_command_sync(*args, **kwargs):
    from app.services.execution import subprocess as execute

    execute.MAX_SUBPROCESS_OUTPUT_CHARS = MAX_SUBPROCESS_OUTPUT_CHARS
    execute.SUBPROCESS_TRUNCATION_MARKER = SUBPROCESS_TRUNCATION_MARKER
    return execute.run_command_sync(*args, **kwargs)


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
