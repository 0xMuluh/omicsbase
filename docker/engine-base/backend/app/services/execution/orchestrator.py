"""Project-runner orchestration for execution contracts and Quarto reports."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable

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
)
from app.services.execution.subprocess import run_command as _run_command
from app.services.capability_contract import CapabilityContractError, load_capability_contract
from app.services.execution_contract import ExecutionContractError, load_execution_contract
from app.services.report_artifacts import discover_and_write_report_manifest


async def run_project_unlocked(
    project_dir: str,
    progress_callback: Callable[[str, str, str], None] | None = None,
    start_page: str | None = None,
    run_data: bool | None = None,
    target_pages: list[str] | None = None,
    resume_from_step: str | None = None,
    requested_source_dir: str | None = None,
    command_runner=None,
) -> dict:
    """Execute the analysis project incrementally.
    
    The old path ran main.R, which rendered the whole Quarto website every time.
    This path renders data prep only when stale, then renders one QMD page at a
    time so failures preserve generated source and complete page diagnostics without publishing a partial report.
    """
    project_path = Path(project_dir)
    output_dir = project_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    # Remove any previous report after artifact baselines are captured below.
    result = {"status": "completed", "logs": [], "errors": [], "pages": [], "failed_pages": []}
    execute_command = command_runner or _run_command
    
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
        (output_dir / "index.html").unlink(missing_ok=True)
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
            (output_dir / "index.html").unlink(missing_ok=True)
            result["status"] = "failed"
            result["errors"].append(
                {"step": "capabilities", "file": ".omicsbase/capabilities.json", "error": message}
            )
            return result
    
    resolved_source: tuple[Path, str] | None = None
    if execution_contract is None:
        try:
            resolved_source = _resolve_quarto_source(project_path, requested_source_dir)
        except ValueError as exc:
            message = str(exc)
            _report("setup", "failed", message)
            (output_dir / "index.html").unlink(missing_ok=True)
            result["status"] = "failed"
            result["errors"].append({"step": "setup", "error": message})
            return result
    
    source_dir = (
        execution_contract.working_path
        if execution_contract is not None
        else resolved_source[0]  # type: ignore[index]
    )
    source_root = (
        execution_contract.working_directory
        if execution_contract is not None
        else resolved_source[1]  # type: ignore[index]
    )
    if not source_dir.exists():
        message = f"Generated source directory not found: {source_dir}"
        _report("setup", "failed", message)
        (output_dir / "index.html").unlink(missing_ok=True)
        result["status"] = "failed"
        result["errors"].append({"step": "setup", "error": message})
        return result
    
    if execution_contract is not None:
        artifact_baseline = _artifact_snapshot(execution_contract)
        (output_dir / "index.html").unlink(missing_ok=True)
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
                success, output = await execute_command(
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
            success, output = await execute_command(
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
            html_artifact = next(
                (relative for relative in execution_contract.artifacts if Path(relative).suffix.lower() == ".html"),
                None,
            )
            manifest = discover_and_write_report_manifest(
                project_path,
                source_dir=working_path,
                preferred_root=(project_path / Path(html_artifact).parent) if html_artifact else None,
            )
            if manifest is None:
                message = "Declared report artifact exists, but no usable HTML report could be discovered."
                _report("verify", "failed", message)
                result["status"] = "failed"
                result["errors"].append({"step": "report_manifest", "error": message})
                return result
            result["report_manifest"] = manifest
            _report("verify", "completed", f"Report preview ready: {manifest['preview_path']}")
            return result
    
    if execution_contract is None:
        (output_dir / "index.html").unlink(missing_ok=True)
    
    data_r = source_dir / "data.R"
    if execution_contract is None and data_r.exists():
        should_run_data = _should_run_data(project_path, data_r) if run_data is None else run_data
        if should_run_data:
            _report("data_r_exec", "running", "Running data.R...")
            success, output = await execute_command(
                ["Rscript", "data.R"],
                cwd=str(source_dir),
                progress_callback=lambda line: _report("data_r_exec", "running", line),
                sandbox_root=str(project_path),
            )
            if not success:
                summary = _extract_error_summary(output)
                _report("data_r_exec", "failed", f"data.R failed: {summary}")
                result["status"] = "failed"
                result["errors"].append({"step": "data_r", "file": f"{source_root}/data.R", "error": output})
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
        if data_r.exists() and execution_contract is None:
            result["status"] = "completed"
            return result
        message = "No QMD files were found to render."
        _report("quarto_pages", "failed", message)
        result["status"] = "failed"
        result["errors"].append({"step": "quarto_pages", "error": message})
        return result
    
    completed_pages = _existing_rendered_pages(output_dir, all_pages)
    
    # Distinguish leaf analysis pages from final assembly pages (e.g. index.qmd, report.qmd, summary.qmd, overview.qmd)
    assembly_names = {"index.qmd", "summary.qmd", "overview.qmd", "report.qmd"}
    if len(pages) > 1 and any(Path(p).name.lower() in assembly_names for p in pages):
        leaf_pages = [p for p in pages if Path(p).name.lower() not in assembly_names]
        assembly_pages = [p for p in pages if Path(p).name.lower() in assembly_names]
    elif len(pages) > 1:
        leaf_pages = pages[:-1]
        assembly_pages = pages[-1:]
    else:
        leaf_pages = pages
        assembly_pages = []
    
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
                result["failed_pages"].append(page)
                result["errors"].append({"step": "qmd", "file": source_file, "error": message})
                _write_incremental_index(output_dir, all_pages, completed_pages, failed_page=page)
                return False
    
            _report(step_id, "running", f"Rendering {page}")
            success, output = await execute_command(
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
                result["failed_pages"].append(page)
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
    
    # Render leaf analysis pages concurrently, gathering all results
    if leaf_pages:
        await asyncio.gather(*[_render_single(p) for p in leaf_pages])
    
    # Render assembly pages after every leaf has had a chance to finish. This
    # preserves the full diagnostic set when several pages fail in one run.
    for page in assembly_pages:
        await _render_single(page)
    
    if result["errors"]:
        # Assembly may have emitted an index even when a leaf failed; do not publish it.
        (output_dir / "index.html").unlink(missing_ok=True)
        result["status"] = "failed"
        return result
    
    if execution_contract is not None:
        artifact_errors = _declared_artifact_errors(
            execution_contract,
            baseline=artifact_baseline,
        )
        if artifact_errors:
            message = "; ".join(artifact_errors)
            _report("verify", "failed", message)
            (output_dir / "index.html").unlink(missing_ok=True)
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
        if not index_html.exists() and any(output_dir.glob("*.html")):
            _write_incremental_index(output_dir, all_pages, completed_pages)
        if index_html.exists():
            _report("verify", "completed", f"Output verified: {index_html}")
        else:
            message = "Quarto completed without producing a report entry page"
            _report("verify", "failed", message)
            result["status"] = "failed"
            result["errors"].append({"step": "artifact", "error": message})
            return result
        manifest = discover_and_write_report_manifest(
            project_path,
            source_dir=source_dir,
            expected_pages=all_pages,
        )
        if manifest is None:
            message = "Quarto completed, but no usable HTML report could be discovered"
            _report("verify", "failed", message)
            result["status"] = "failed"
            result["errors"].append({"step": "report_manifest", "error": message})
            return result
        result["report_manifest"] = manifest
        _report("verify", "completed", f"Report preview ready: {manifest['preview_path']}")

    
    return result
    
