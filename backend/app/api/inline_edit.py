"""Fast inline code editing API endpoint for Monaco editor (Cmd+K / Cmd+I)."""

from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_project_for_tenant
from app.database import get_db
from app.services.edit_engine import sha256_bytes
from app.services.llm import stream_llm_text

router = APIRouter(prefix="/api/inline-edit", tags=["inline-edit"])


class InlineEditRequest(BaseModel):
    project_id: str = Field(min_length=1, description="Project that owns the file being edited")
    path: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=4000)
    selection: str | None = None
    content: str = Field(default="")
    base_sha256: str = Field(min_length=64, max_length=128, description="Hash of the saved file the draft was based on")
    project_context: str | None = None
    error_context: str | None = None


INLINE_EDIT_SYSTEM_PROMPT = """You are an expert real-time R, Quarto, and Python code editor for OmicsBase.
You receive a target file path, current file content, an optional selected code snippet, project domain context (dataset schema/variables), optional error diagnostics, and an edit request.

CRITICAL INSTRUCTIONS:
1. If a selection is provided, produce ONLY the replacement code for that selection.
2. If no selection is provided, produce the updated full file code.
3. Do NOT include markdown code fences (```) or conversational prose.
4. Preserve existing imports, indentation, and formatting conventions.
5. Use domain-specific R/Bioconductor/tidyverse packages (ggplot2, phyloseq, DESeq2, mia) accurately matching the project context."""


def _ndjson(event: dict) -> str:
    return json.dumps(event, default=str) + "\n"


@router.post("")
async def inline_edit(
    data: InlineEditRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Stream inline code replacements directly for Monaco editor with rich context."""
    project = get_project_for_tenant(db, data.project_id, tenant_id)
    if not project.project_dir:
        raise HTTPException(status_code=404, detail="Project workspace is not generated")
    from app.services.apply_edits import safe_resolve_path

    base = Path(project.project_dir).resolve()
    target = safe_resolve_path(base, data.path)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="Inline edit target is not a project file")
    if target.suffix.lower() not in {".r", ".qmd", ".yml", ".yaml", ".md", ".txt", ".csv", ".tsv", ".json", ".html", ".css", ".js", ".ts", ".tsx"}:
        raise HTTPException(status_code=400, detail="Inline AI editing is restricted to text source files")
    actual_sha256 = sha256_bytes(target.read_bytes()) or ""
    if data.base_sha256 != actual_sha256:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "edit_conflict",
                "message": "The file changed since this inline draft was loaded.",
                "actual_sha256": actual_sha256,
            },
        )

    target_context = f"Selected snippet to replace:\n{data.selection}\n\n" if data.selection else ""
    proj_ctx = f"Project Domain Context:\n{data.project_context}\n\n" if data.project_context else ""
    err_ctx = f"Recent Execution Diagnostics / Error:\n{data.error_context}\n\n" if data.error_context else ""

    user_prompt = (
        f"Target File: {data.path}\n"
        f"Saved File SHA-256: {data.base_sha256}\n"
        f"User Edit Request: {data.prompt}\n\n"
        f"{proj_ctx}"
        f"{err_ctx}"
        f"{target_context}"
        f"Full File Content:\n{data.content}\n"
    )

    async def event_stream():
        yield _ndjson({"type": "start"})
        try:
            async for token in stream_llm_text(
                system_prompt=INLINE_EDIT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=4000,
            ):
                yield _ndjson({"type": "token", "token": token})
            yield _ndjson({"type": "done"})
        except Exception as exc:
            yield _ndjson({"type": "error", "error": str(exc)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")

