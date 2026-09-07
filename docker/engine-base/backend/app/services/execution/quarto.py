"""Quarto source discovery, rendering policy, and report assembly helpers."""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path

import yaml

from app.services.execution_contract import ExecutionContractError, load_execution_contract

logger = logging.getLogger(__name__)


def _resolve_quarto_source(
    project_path: Path,
    requested_source_dir: str | None,
) -> tuple[Path, str]:
    """Resolve one project-local Quarto source without imposing a report layout."""
    project_path = project_path.resolve()
    forbidden_roots = {"data", "output", ".omicsbase", ".git"}

    if requested_source_dir:
        raw = requested_source_dir.strip().replace("\\", "/")
        relative = Path(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source_dir must be a project-relative directory")
        candidate = (project_path / relative).resolve()
        try:
            resolved_relative = candidate.relative_to(project_path)
        except ValueError as exc:
            raise ValueError("source_dir is outside this project") from exc
        if resolved_relative.parts and resolved_relative.parts[0] in forbidden_roots:
            raise ValueError("source_dir cannot be data, output, or private runtime state")
        if not candidate.is_dir():
            raise ValueError(f"Quarto source directory not found: {raw}")
        if not (candidate / "_quarto.yml").is_file():
            raise ValueError(f"Quarto source directory has no _quarto.yml: {raw}")
        return candidate, resolved_relative.as_posix()

    candidates: list[Path] = []
    for config_path in project_path.rglob("_quarto.yml"):
        relative = config_path.relative_to(project_path)
        if relative.parts and relative.parts[0] in forbidden_roots:
            continue
        candidates.append(config_path.parent.resolve())
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        candidate = candidates[0]
        return candidate, candidate.relative_to(project_path).as_posix()
    if not candidates:
        qmd_files = []
        for qmd_path in project_path.rglob("*.qmd"):
            relative = qmd_path.relative_to(project_path)
            if relative.parts and relative.parts[0] in forbidden_roots:
                continue
            qmd_files.append(qmd_path.resolve())
        if qmd_files:
            common_root = Path(os.path.commonpath([str(path.parent) for path in qmd_files]))
            return common_root, common_root.relative_to(project_path).as_posix()
        raise ValueError(
            "No project-local Quarto source was found. Create _quarto.yml and at least one QMD file, then call run_project again."
        )
    choices = ", ".join(path.relative_to(project_path).as_posix() for path in candidates)
    raise ValueError(
        "Multiple Quarto source directories were found. Call run_project with source_dir set to one of: "
        + choices
    )


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

    return sorted(
        path.relative_to(code_dir).as_posix()
        for path in code_dir.rglob("*.qmd")
    )


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
            if baseline.get(relative) is not None:
                errors.append(f"Declared artifact was not refreshed by this run: {relative}")
            else:
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
    """Publish the report index only after every requested page succeeds."""
    if failed_page is not None or set(pages) - set(completed_pages):
        return
    # A rendered entry page (e.g. index.qmd, report.qmd) is the report itself. Replacing it with an iframe
    # whose src is index.html creates a self-reference and a blank preview.
    has_rendered_index = any(Path(page).name.lower() in {"index.qmd", "report.qmd"} for page in pages)
    if has_rendered_index and (output_dir / "index.html").is_file():
        return
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
