"""Project workspace artifact paths (results tables, note execution outputs)."""

from __future__ import annotations

from pathlib import Path


def list_project_result_artifacts(project) -> list[str]:
    """Workspace result tables plus tables from project-attached note executions."""
    base = _project_base(project)
    if not base:
        return []
    found: list[str] = []
    for pattern in (
        "output/results/*",
        ".omicsbase/note-executions/*/tables/*",
        "output/derived/note-executions/*/tables/*",
    ):
        for path in sorted(base.glob(pattern)):
            if path.is_file() and path.suffix.lower() in {".csv", ".tsv", ".json", ".txt"}:
                relative = path.relative_to(base).as_posix()
                if relative not in found:
                    found.append(relative)
    return found[:100]


def _project_base(project) -> Path | None:
    if not project.project_dir:
        return None
    base = Path(project.project_dir).resolve()
    return base if base.exists() else None
