"""File serving API — serves project files and rendered output."""

from __future__ import annotations

import subprocess
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.models.project import Project
from app.services.edit_engine import EditBusy, EditConflict, EditEngineError, EditOperation, EditPolicy, apply_transaction, sha256_bytes
from app.services.file_inspector import TABULAR_PREVIEW_EXTENSIONS, preview_tabular_file

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])

TEXT_EXTENSIONS = {".r", ".qmd", ".yml", ".yaml", ".md", ".txt", ".csv", ".tsv", ".json", ".html", ".css", ".js"}
EDITABLE_EXTENSIONS = {".r", ".qmd", ".yml", ".yaml", ".md", ".txt", ".csv", ".tsv", ".json"}
MAX_EDIT_BYTES = 2_000_000
CHUNK_TIMEOUT_SECONDS = 180
PREVIEW_MAX_ROWS = 100
PROJECT_READ_ONLY_FILES = frozenset({
    "adaptation_manifest.json",
    "execution_contract.json",
    "omicsbase-pack.yaml",
    "report_pack.yaml",
})
PROJECT_READ_ONLY_DIRS = ("data/", "output/", ".omicsbase/")


def _is_read_only_project_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized in PROJECT_READ_ONLY_FILES or any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in PROJECT_READ_ONLY_DIRS
    )


def _is_editable_project_path(relative_path: str, path: Path) -> bool:
    return not _is_read_only_project_path(relative_path) and path.suffix.lower() in EDITABLE_EXTENSIONS


class FileContentUpdate(BaseModel):
    content: str


class ChunkRunRequest(BaseModel):
    code: str
    file_path: str | None = None



@router.get("/tree")
def get_file_tree(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Get the project file tree."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found or not generated")

    tree = _build_tree(Path(project.project_dir))
    return tree


@router.get("/preview/{file_path:path}")
def get_file_preview(
    project_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Return a tabular preview for CSV/TSV/Excel/SPSS project files."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    full_path = _resolve_project_file(project, file_path)
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    suffix = full_path.suffix.lower()
    if suffix not in TABULAR_PREVIEW_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"No tabular preview for {suffix or 'unknown'} files")

    preview = preview_tabular_file(str(full_path), max_rows=PREVIEW_MAX_ROWS)
    preview["path"] = file_path
    return preview


@router.get("/content/{file_path:path}")
def get_file_content(
    project_id: str,
    file_path: str,
    response: Response,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Get a project file and its content hash for optimistic browser saves."""
    project = get_project_for_tenant(db, project_id, tenant_id)

    full_path = _resolve_project_file(project, file_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if full_path.suffix.lower() in TEXT_EXTENSIONS:
        raw = full_path.read_bytes()
        digest = sha256_bytes(raw) or ""
        response.headers["ETag"] = f'"{digest}"'
        return {
            "content": raw.decode("utf-8", errors="replace"),
            "path": file_path,
            "type": "text",
            "sha256": digest,
        }

    # For other files, serve as download
    return FileResponse(str(full_path), filename=full_path.name)


@router.patch("/content/{file_path:path}")
def update_file_content(
    project_id: str,
    file_path: str,
    data: FileContentUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Update an editable file through the shared CAS-protected edit engine."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found")

    full_path = _resolve_project_file(project, file_path)
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    relative_path = full_path.relative_to(Path(project.project_dir).resolve()).as_posix()
    if _is_read_only_project_path(relative_path):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "protected_project_file",
                "message": "Project inputs, outputs, and execution contracts are read-only.",
                "path": relative_path,
            },
        )
    if full_path.suffix.lower() not in EDITABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type is not editable: {full_path.suffix}")

    encoded = data.content.encode("utf-8")
    if len(encoded) > MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail="File is too large to edit in the browser")

    current = full_path.read_bytes()
    current_sha256 = sha256_bytes(current) or ""
    supplied = (if_match or "").strip()
    if not supplied or supplied == "*":
        raise HTTPException(
            status_code=428,
            detail={
                "code": "edit_precondition_required",
                "message": "Send the file SHA-256 in If-Match before saving.",
                "actual_sha256": current_sha256,
            },
        )
    expected_sha256 = supplied.strip('"')
    if expected_sha256 != current_sha256:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "edit_conflict",
                "message": "The file changed since it was loaded; reload before saving.",
                "expected_sha256": expected_sha256,
                "actual_sha256": current_sha256,
            },
        )

    try:
        result = apply_transaction(
            project.project_dir,
            [
                EditOperation(
                    path=relative_path,
                    kind="rewrite",
                    content=data.content,
                    base_sha256=expected_sha256,
                    reason="Browser editor save",
                )
            ],
            origin="browser",
            summary=f"Save {relative_path}",
            policy=EditPolicy(
                allowed_extensions=frozenset(EDITABLE_EXTENSIONS),
                allow_create=False,
                allow_delete=False,
                require_base_for_rewrite=True,
            ),
            validate=True,
            lock_timeout=0,
        )
    except EditBusy as exc:
        raise HTTPException(status_code=423, detail=exc.to_dict()) from exc
    except EditConflict as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    except EditEngineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

    from app.services.agent_runtime import record_agent_action, refresh_project_memory
    from app.services.project_edit_index import record_project_edit
    record_project_edit(db, project, result)
    refresh_project_memory(db, project)
    record_agent_action(db, project, "file_edit", "completed", f"Saved {relative_path}", files=[relative_path])
    new_sha256 = sha256_bytes(encoded) or ""
    response.headers["ETag"] = f'"{new_sha256}"'

    return {
        "content": data.content,
        "path": relative_path,
        "type": "text",
        "saved": True,
        "sha256": new_sha256,
        "transaction_id": result.transaction_id,
    }


@router.post("/run-chunk")
async def run_code_chunk(
    project_id: str,
    data: ChunkRunRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Render a single R code chunk to HTML and return execution output via hardened runner."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found")
    if not data.code.strip():
        raise HTTPException(status_code=400, detail="No code supplied")

    project_dir = Path(project.project_dir).resolve()
    code_dir = project_dir / "code"
    if not code_dir.exists():
        code_dir = project_dir

    run_id = uuid.uuid4().hex
    run_dir = project_dir / ".chunk-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    chunk_qmd = code_dir / f"__chunk_run_{run_id}.qmd"
    output_file = run_dir / "chunk.html"
    source_label = data.file_path or "selection"
    started = time.monotonic()

    chunk_qmd.write_text(_build_chunk_qmd(data.code, source_label))
    try:
        from app.services.runner import _run_command

        cmd = ["quarto", "render", chunk_qmd.name, "--output", "chunk.html", "--output-dir", str(run_dir)]
        success, stdout = await _run_command(cmd, cwd=str(code_dir), timeout=CHUNK_TIMEOUT_SECONDS)
        returncode = 0 if success else 1
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Quarto is not installed or not available on PATH") from exc
    finally:
        chunk_qmd.unlink(missing_ok=True)

    duration_seconds = round(time.monotonic() - started, 2)
    status = "completed" if success and output_file.exists() else "failed"
    html_url = f"/projects/{project_id}/files/chunk-output/{run_id}/chunk.html" if output_file.exists() else None

    from app.services.agent_runtime import record_agent_action
    record_agent_action(
        db,
        project,
        "chunk_run",
        status,
        f"Ran chunk from {source_label}",
        {"returncode": returncode, "duration_seconds": duration_seconds},
        files=[data.file_path] if data.file_path else None,
    )

    return {
        "status": status,
        "run_id": run_id,
        "stdout": stdout,
        "error": None if status == "completed" else stdout,
        "duration_seconds": duration_seconds,
        "html_url": html_url,
    }


@router.get("/chunk-output/{run_id}/{file_path:path}")
def serve_chunk_output(
    project_id: str,
    run_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Serve rich HTML/assets generated by a single chunk run."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found")

    run_dir = (Path(project.project_dir) / ".chunk-runs" / run_id).resolve()
    full_path = (run_dir / file_path).resolve()
    try:
        full_path.relative_to(run_dir)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    media_types = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
    }
    return FileResponse(str(full_path), media_type=media_types.get(full_path.suffix.lower(), "application/octet-stream"))


@router.get("/report/{file_path:path}")
def serve_report_file(
    project_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Serve a rendered report file (HTML, CSS, JS, images)."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found")

    full_path = Path(project.project_dir) / "output" / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    # Security check
    try:
        full_path.resolve().relative_to(Path(project.project_dir).resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine media type
    media_types = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
    }
    media_type = media_types.get(full_path.suffix.lower(), "application/octet-stream")

    return FileResponse(str(full_path), media_type=media_type)


@router.get("/download")
def download_project(
    project_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Download the entire project as a ZIP file."""
    project = get_project_for_tenant(db, project_id, tenant_id)
    if not project or not project.project_dir:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(project.project_dir)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Create ZIP in memory
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in project_path.rglob("*"):
            if file_path.is_file():
                # Skip large binary files and hidden directories
                relative = file_path.relative_to(project_path)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                zf.write(file_path, str(relative))

    buffer.seek(0)
    safe_name = project.name.replace(" ", "_").lower()

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_project.zip"'},
    )


def _resolve_project_file(project: Project, file_path: str) -> Path:
    """Resolve a project-relative file path and enforce project containment using safe_resolve_path."""
    from app.services.apply_edits import safe_resolve_path

    resolved = safe_resolve_path(project.project_dir, file_path)
    if not resolved:
        raise HTTPException(status_code=403, detail="Access denied: Invalid or escaping path")
    return resolved


def _build_chunk_qmd(code: str, source_label: str) -> str:
    safe_label = source_label.replace('"', "'")
    return f"""---
title: "Chunk output"
format:
  html:
    embed-resources: true
    theme: darkly
execute:
  warning: true
  message: true
---

````{{r}}
#| label: chunk-output
#| echo: true
# Source: {safe_label}
{code}
````
"""


def _build_tree(root: Path, prefix: str = "") -> list[dict]:
    """Build a file tree structure for the frontend."""
    if not root.exists():
        return []

    items = []
    for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        # Skip hidden files and common non-essential dirs
        if item.name.startswith(".") or item.name in {"__pycache__", "node_modules", "_freeze", "site_libs"}:
            continue

        relative_path = f"{prefix}{item.name}" if not prefix else f"{prefix}/{item.name}"

        if item.is_dir():
            children = _build_tree(item, relative_path)
            items.append({
                "name": item.name,
                "path": relative_path,
                "type": "directory",
                "children": children,
            })
        else:
            items.append({
                "name": item.name,
                "path": relative_path,
                "type": "file",
                "size": item.stat().st_size,
                "extension": item.suffix,
                "editable": _is_editable_project_path(relative_path, item),
            })

    return items
