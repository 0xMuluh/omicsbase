"""Validated promotion of tested notebook code into a workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.notes import CellExecution, NoteCell, NoteCellRevision, NoteThread
from app.models.project import Project
from app.services.note_execution import input_fingerprint


def promote_cell_to_workspace(
    db: Session,
    thread: NoteThread,
    arguments: dict[str, Any],
    *,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Promote only an immutable, successfully executed notebook revision."""
    from app.services.edit_engine import (
        EditConflict,
        EditEngineError,
        EditOperation,
        EditPolicy,
        apply_transaction,
        is_path_locked,
        sha256_bytes,
    )

    if not thread.project_id:
        return {"status": "error", "error": "This notebook is not attached to a project. Attach it first, then promote.", "turn_id": turn_id}
    project = db.query(Project).filter(Project.id == thread.project_id).first()
    base = Path(project.project_dir).resolve() if project and project.project_dir else None
    if not base or not base.exists():
        return {"status": "error", "error": "The project has no generated workspace yet. Build the report first, then promote.", "turn_id": turn_id}

    cell_id = str(arguments.get("cell_id") or "").strip()
    revision_id = str(arguments.get("revision_id") or "").strip()
    execution_id = str(arguments.get("execution_id") or "").strip()
    relative_path = str(arguments.get("path") or "").strip().replace("\\", "/")
    if relative_path.startswith("code/"):
        relative_path = relative_path[5:]
    if not relative_path:
        return {"status": "error", "error": "Promotion requires cell_id, revision_id, execution_id, and a code-relative path.", "turn_id": turn_id}
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        return {"status": "error", "error": "The path escapes the project code directory.", "turn_id": turn_id}
    if not relative_path.lower().endswith((".r", ".qmd", ".md")):
        return {"status": "error", "error": "The promotion path must stay inside code/ and use .R, .qmd, or .md.", "turn_id": turn_id}
    project_relative = f"code/{relative_path}"
    target = (base / project_relative).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        return {"status": "error", "error": "The path escapes the project code directory.", "turn_id": turn_id}
    strategy = str(arguments.get("strategy") or "create_only").strip().lower()
    supplied_base_sha256 = str(arguments.get("base_sha256") or "").strip().strip('"')
    if strategy not in {"replace", "append", "create_only"}:
        return {"status": "error", "error": "Promotion strategy must be replace, append, or create_only.", "turn_id": turn_id}
    if is_path_locked(base, project_relative):
        return {"status": "error", "error": "The promotion path is locked by workspace policy.", "turn_id": turn_id}
    if not cell_id or not revision_id or not execution_id:
        return {"status": "error", "error": "Promotion requires cell_id, revision_id, execution_id, and a code-relative path.", "turn_id": turn_id}

    cell = db.query(NoteCell).filter(NoteCell.id == cell_id, NoteCell.thread_id == thread.id).first()
    revision = db.query(NoteCellRevision).filter(NoteCellRevision.id == revision_id, NoteCellRevision.cell_id == cell_id).first()
    execution = db.query(CellExecution).filter(CellExecution.id == execution_id, CellExecution.revision_id == revision_id).first()
    if cell is None or revision is None or execution is None:
        return {"status": "error", "error": "The supplied notebook provenance does not belong to this thread.", "turn_id": turn_id}
    if str(revision.cell_type or "") != "code" or str(revision.language or "r").lower() not in {"r", "rscript"}:
        return {"status": "error", "error": "Only successfully executed R code cells can be promoted.", "turn_id": turn_id}
    if execution.status != "completed":
        return {"status": "error", "error": f"The referenced execution is not successful (status: {execution.status}).", "turn_id": turn_id}
    expected_input = input_fingerprint(revision.content, revision.language, execution.parameters)
    if not execution.input_fingerprint or execution.input_fingerprint != expected_input:
        return {"status": "error", "error": "Execution provenance does not match the immutable cell revision.", "turn_id": turn_id}

    content = str(revision.content or "")
    if not content.strip():
        return {"status": "error", "error": "The tested cell is empty and cannot be promoted.", "turn_id": turn_id}
    if len(content.encode("utf-8")) > 200_000:
        return {"status": "error", "error": "The promoted content exceeds the 200 KB limit.", "turn_id": turn_id}
    existing = target.read_bytes() if target.exists() else None
    actual_base_sha256 = sha256_bytes(existing)
    if existing is not None and strategy == "create_only":
        return {"status": "error", "error": f"{project_relative} already exists; choose append or replace with an explicit base_sha256.", "turn_id": turn_id}
    if existing is not None and not supplied_base_sha256:
        return {"status": "error", "error": "Updating an existing promoted file requires base_sha256 from the current workspace.", "code": "edit_precondition_required", "actual_sha256": actual_base_sha256, "turn_id": turn_id}
    if existing is not None and supplied_base_sha256 != actual_base_sha256:
        return {"status": "error", "error": "The promoted target changed since it was inspected; reload it before promotion.", "code": "edit_conflict", "expected_sha256": supplied_base_sha256, "actual_sha256": actual_base_sha256, "turn_id": turn_id}
    if existing is None:
        operation = EditOperation(path=project_relative, kind="create", content=content, reason=f"Promote note execution {execution_id}")
    elif strategy == "append":
        operation = EditOperation(path=project_relative, kind="replace", search="", replace=content, base_sha256=supplied_base_sha256, reason=f"Promote note execution {execution_id}")
    else:
        operation = EditOperation(path=project_relative, kind="rewrite", content=content, base_sha256=supplied_base_sha256, reason=f"Promote note execution {execution_id}")

    try:
        result = apply_transaction(
            base,
            [operation],
            origin="note_promotion",
            summary=f"Promote tested note cell to {project_relative}",
            validate=True,
            policy=EditPolicy(
                allowed_extensions=frozenset({".r", ".qmd", ".md"}),
                allow_create=True,
                allow_delete=False,
                require_base_for_rewrite=True,
            ),
            lock_timeout=0,
        )
    except EditConflict as exc:
        return {"status": "error", "error": str(exc), "code": exc.code, "details": exc.details, "turn_id": turn_id}
    except EditEngineError as exc:
        return {"status": "error", "error": str(exc), "code": exc.code, "details": exc.details, "turn_id": turn_id}

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    from app.services.project_edit_index import record_project_edit

    record_project_edit(db, project, result)
    refresh_project_memory(db, project)
    record_agent_action(
        db,
        project,
        "file_edit",
        "completed",
        f"Promoted tested note cell to {project_relative}",
        {"transaction_id": result.transaction_id, "note_thread_id": str(thread.id), "cell_id": cell_id, "revision_id": revision_id, "execution_id": execution_id, "strategy": strategy},
        files=[project_relative],
    )
    return {"status": "ok", "path": project_relative, "promoted": True, "turn_id": turn_id, "transaction_id": result.transaction_id, "cell_id": cell_id, "revision_id": revision_id, "execution_id": execution_id, "content_sha256": sha256_bytes(content.encode("utf-8"))}

