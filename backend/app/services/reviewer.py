"""Post-render review checks for generated analysis projects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.execution_contract import (
    ExecutionContractError,
    load_execution_contract,
)
from app.services.execution_provenance import list_execution_provenance
from app.services.edit_validation import validate_text

REQUIRED_CODE_FILES = ("data.R", "funct.R", "_quarto.yml", "main.R")
ERROR_MARKERS = ("error in", "execution halted", "quitting from lines")
ABSOLUTE_PATH_PATTERN = re.compile(r'["\']/(?:home|Users|tmp|var)/[^"\']+["\']')


def _check(name: str, passed: bool, detail: str, *, severity: str = "failed") -> dict[str, str]:
    return {"name": name, "status": "passed" if passed else severity, "detail": detail}


def review_render_output(project_dir: str) -> dict[str, Any]:
    """Validate that the render produced a usable, structurally sound report artifact."""
    base = Path(project_dir)
    output_dir = base / "output"
    checks: list[dict[str, str]] = []

    try:
        execution_contract = load_execution_contract(base)
    except ExecutionContractError as exc:
        return {
            "status": "failed",
            "summary": f"Invalid execution contract: {exc}",
            "checks": [
                _check(
                    "execution_contract",
                    False,
                    f"Invalid execution contract: {exc}",
                )
            ],
        }

    artifact_relatives = (
        list(execution_contract.artifacts)
        if execution_contract is not None
        else ["output/index.html"]
    )
    if execution_contract is None and not output_dir.exists():
        return {
            "status": "failed",
            "summary": "Render did not create an output directory",
            "checks": checks,
        }
    for relative in artifact_relatives:
        artifact_path = base / relative
        check_id = re.sub(r"[^A-Za-z0-9]+", "_", relative).strip("_")
        checks.append(
            _check(
                f"artifact_{check_id}",
                artifact_path.is_file(),
                f"{relative} {'exists' if artifact_path.is_file() else 'missing'}",
            )
        )
    missing_artifacts = [
        relative for relative in artifact_relatives if not (base / relative).is_file()
    ]
    if missing_artifacts:
        return {
            "status": "failed",
            "summary": "Render did not produce declared artifact(s): "
            + ", ".join(missing_artifacts),
            "checks": checks,
        }

    html_relative = next(
        relative for relative in artifact_relatives if Path(relative).suffix.lower() == ".html"
    )
    html_path = base / html_relative
    html = html_path.read_text(errors="replace")
    checks.extend(
        [
            _check(
                "output_directory",
                output_dir.exists(),
                "output/ exists" if output_dir.exists() else "output/ missing",
                severity="warning",
            ),
            _check(
                "html_size",
                len(html) > 500,
                f"{html_relative} has {len(html)} characters",
                severity="warning",
            ),
        ]
    )

    code_dir = base / "code"
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

    if execution_contract is None:
        required_sources = [f"code/{filename}" for filename in REQUIRED_CODE_FILES]
    else:
        checks.append(_check("execution_contract", True, "ReportPack execution contract is valid"))
        required_sources = [f"{source_root}/_quarto.yml"]
        if execution_contract.entrypoint:
            required_sources.append(execution_contract.entrypoint)
        required_sources.extend(step.path for step in execution_contract.steps)
        required_sources = list(dict.fromkeys(required_sources))

    for relative in required_sources:
        path = base / relative
        check_id = re.sub(r"[^A-Za-z0-9]+", "_", relative).strip("_")
        checks.append(
            _check(
                f"source_{check_id}",
                path.exists(),
                f"{relative} {'exists' if path.exists() else 'missing'}",
            )
        )

    if execution_contract is not None:
        validator_steps = [step for step in execution_contract.steps if step.role == "validator"]
        if validator_steps:
            latest = list_execution_provenance(base, limit=1)
            if not latest:
                checks.append(
                    _check(
                        "validator_provenance",
                        False,
                        "No execution provenance record is available for declared validator steps.",
                        severity="warning",
                    )
                )
            else:
                evidence = {item.get("step_id"): item for item in latest[0].get("validators") or []}
                missing = [step.step_id for step in validator_steps if step.step_id not in evidence]
                not_run = [step.step_id for step in validator_steps if evidence.get(step.step_id, {}).get("status") != "completed"]
                targeted = bool(latest[0].get("target_pages")) and not latest[0].get("resume_from_step")
                if targeted and not_run:
                    checks.append(
                        _check(
                            "validator_provenance",
                            False,
                            "Validator steps were intentionally skipped for a targeted page render: "
                            + ", ".join(not_run),
                            severity="warning",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "validator_provenance",
                            not missing and not not_run,
                            "All declared validator steps completed with recorded input hashes."
                            if not missing and not not_run
                            else "Validator evidence is incomplete or a declared validator did not complete: "
                            + ", ".join(missing + not_run),
                        )
                    )

    qmd_files = sorted(source_dir.glob("**/*.qmd")) if source_dir.exists() else []
    checks.append(
        _check(
            "qmd_pages",
            len(qmd_files) > 0,
            f"Found {len(qmd_files)} Quarto page(s) under {source_root}/",
        )
    )

    if qmd_files:
        quarto_issues = []
        for page in qmd_files:
            relative = page.relative_to(base).as_posix()
            validation = validate_text(relative, page.read_bytes())
            quarto_issues.extend(issue for issue in validation.issues if issue.severity == "error")
        checks.append(
            _check(
                "quarto_semantics",
                not quarto_issues,
                "Quarto page front matter and UTF-8 semantics are valid."
                if not quarto_issues
                else "Quarto semantic validation failed: " + "; ".join(
                    f"{issue.path}: {issue.message}" for issue in quarto_issues[:4]
                ),
            )
        )

    if qmd_files:
        checks.append(
            _check(
                "index_page",
                any(page.name in {"index.qmd", "index.Qmd"} for page in qmd_files),
                f"{source_root}/index.qmd present"
                if any(page.name.lower() == "index.qmd" for page in qmd_files)
                else f"{source_root}/index.qmd missing",
                severity="warning",
            )
        )

    session_info_in_html = "sessioninfo" in html.lower() or "session info" in html.lower()
    session_info_in_source = any(
        "sessioninfo" in path.read_text(errors="replace").lower()
        for path in list(source_dir.glob("**/*.R")) + qmd_files
    )
    checks.append(
        _check(
            "session_info",
            session_info_in_html or session_info_in_source,
            "sessionInfo() referenced in source or rendered HTML",
            severity="warning",
        )
    )

    has_navigation = "<nav" in html.lower() or "sidebar" in html.lower() or "navbar" in html.lower()
    checks.append(
        _check(
            "navigation",
            has_navigation,
            "Rendered HTML includes navigation structure",
            severity="warning",
        )
    )

    absolute_path_hits: list[str] = []
    for source_path in list(source_dir.glob("**/*.R")) + qmd_files:
        for match in ABSOLUTE_PATH_PATTERN.findall(source_path.read_text(errors="replace")):
            absolute_path_hits.append(f"{source_path.name}: {match}")
    checks.append(
        _check(
            "portable_paths",
            not absolute_path_hits,
            "No hardcoded absolute paths in R/QMD sources"
            if not absolute_path_hits
            else f"Absolute paths found: {', '.join(absolute_path_hits[:3])}",
            severity="warning",
        )
    )

    if any(marker in html.lower() for marker in ERROR_MARKERS):
        checks.append(_check("embedded_errors", False, "Rendered HTML appears to contain runtime errors"))
        return {"status": "failed", "summary": "Rendered report contains error text", "checks": checks}

    if any(check["status"] == "failed" for check in checks):
        failed = [check["name"] for check in checks if check["status"] == "failed"]
        return {"status": "failed", "summary": f"Report structure check failed: {', '.join(failed)}", "checks": checks}

    status = "warning" if any(check["status"] == "warning" for check in checks) else "passed"
    summary = "Rendered report passed artifact review" if status == "passed" else "Rendered report passed with warnings"
    return {"status": status, "summary": summary, "checks": checks}
