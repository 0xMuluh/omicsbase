"""Post-render review checks for generated analysis projects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REQUIRED_CODE_FILES = ("data.R", "funct.R", "_quarto.yml", "main.R")
ERROR_MARKERS = ("error in", "execution halted", "quitting from lines")
ABSOLUTE_PATH_PATTERN = re.compile(r'["\']/(?:home|Users|tmp|var)/[^"\']+["\']')


def _check(name: str, passed: bool, detail: str, *, severity: str = "failed") -> dict[str, str]:
    return {"name": name, "status": "passed" if passed else severity, "detail": detail}


def review_render_output(project_dir: str) -> dict[str, Any]:
    """Validate that the render produced a usable, structurally sound report artifact."""
    base = Path(project_dir)
    output_dir = base / "output"
    index_html = output_dir / "index.html"
    checks: list[dict[str, str]] = []

    if not output_dir.exists():
        return {"status": "failed", "summary": "Render did not create an output directory", "checks": checks}
    if not index_html.exists():
        return {"status": "failed", "summary": "Render did not produce output/index.html", "checks": checks}

    html = index_html.read_text(errors="replace")
    checks.extend([
        _check("output_directory", True, "output/ exists"),
        _check("index_html", True, "output/index.html exists"),
        _check(
            "html_size",
            len(html) > 500,
            f"index.html has {len(html)} characters",
            severity="warning",
        ),
    ])

    code_dir = base / "code"
    for filename in REQUIRED_CODE_FILES:
        path = code_dir / filename
        checks.append(
            _check(
                f"source_{filename}",
                path.exists(),
                f"code/{filename} {'exists' if path.exists() else 'missing'}",
            )
        )

    qmd_files = sorted(code_dir.glob("**/*.qmd")) if code_dir.exists() else []
    checks.append(
        _check(
            "qmd_pages",
            len(qmd_files) > 0,
            f"Found {len(qmd_files)} Quarto page(s) under code/",
        )
    )

    if qmd_files:
        checks.append(
            _check(
                "index_page",
                any(page.name in {"index.qmd", "index.Qmd"} for page in qmd_files),
                "code/index.qmd present" if any(page.name.lower() == "index.qmd" for page in qmd_files) else "code/index.qmd missing",
                severity="warning",
            )
        )

    session_info_in_html = "sessioninfo" in html.lower() or "session info" in html.lower()
    session_info_in_source = any(
        "sessioninfo" in path.read_text(errors="replace").lower()
        for path in list(code_dir.glob("**/*.R")) + qmd_files
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
    for source_path in list(code_dir.glob("**/*.R")) + qmd_files:
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
