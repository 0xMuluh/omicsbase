"""Filesystem boundary for VS Code project workspaces."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.models.project import Project, UploadedFile


PROJECT_BRIEF_NAME = "PROJECT.md"


def project_workspace_path(project: Project) -> Path:
    """Return the canonical workspace directory for a project."""
    return Path(settings.projects_dir).resolve() / str(project.id)


def ensure_project_workspace(
    project: Project,
    *,
    uploaded_files: list[UploadedFile] | None = None,
) -> Path:
    """Create the project workspace and import any legacy uploads."""
    workspace = project_workspace_path(project)
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    project.project_dir = str(workspace)
    _write_project_brief(project, workspace)

    for uploaded in uploaded_files or []:
        source = Path(uploaded.file_path or "")
        if not source.is_file():
            continue
        destination = data_dir / Path(uploaded.original_name or source.name).name
        if source.resolve() != destination.resolve() and not destination.exists():
            shutil.copy2(source, destination)
        uploaded.file_path = str(destination)

    return workspace


def _write_project_brief(project: Project, workspace: Path) -> None:
    brief = workspace / PROJECT_BRIEF_NAME
    if brief.exists():
        return

    objective = (project.question or project.custom_plan_text or "Define the analysis objective with the agent.").strip()
    context = (project.notes or "").strip()
    lines = [
        f"# {project.name}",
        "",
        "## Objective",
        "",
        objective,
        "",
        "## Workspace",
        "",
        "Input files are in `data/`. Keep analysis source, figures, and reports in this project directory.",
    ]
    if context:
        lines.extend(["", "## Context", "", context])
    brief.write_text("\n".join(lines) + "\n", encoding="utf-8")
