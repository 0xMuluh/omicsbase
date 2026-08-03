"""One-way, provenance-preserving export from a NoteThread to Quarto source."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.notes import CellExecution, NoteCellRevision, NoteThread


def _latest_revision(cell: Any) -> NoteCellRevision | None:
    revisions = list(getattr(cell, "revisions", []) or [])
    return revisions[-1] if revisions else None


def safe_report_slug(value: str | None, thread_id: str) -> str:
    candidate = (value or "").strip().lower()
    candidate = re.sub(r"[^a-z0-9]+", "-", candidate).strip("-")
    if not candidate:
        candidate = "note-" + re.sub(r"[^a-z0-9]", "", str(thread_id).lower())[:12]
    return candidate[:80].rstrip("-") or "note-thread"


def _yaml_title(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _quoted_block(value: str) -> list[str]:
    lines = value.rstrip().splitlines() or [""]
    return ["> " + line if line else ">" for line in lines]


def _fenced_block(language: str, content: str) -> list[str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{{{language}}}", content.rstrip(), fence]


def build_note_qmd(
    db: Session,
    thread: NoteThread,
    *,
    exported_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render the current immutable cell revisions as draft Quarto source."""
    exported_at = exported_at or datetime.now(timezone.utc)
    lines = [
        "---",
        f'title: "{_yaml_title(thread.title or "Untitled note")}"',
        "format: html",
        "execute:",
        "  freeze: auto",
        "---",
        "",
        f"_Exported from OmicsBase NoteThread `{thread.id}`. Export timestamp is retained in report metadata._",
        "",
    ]
    revision_ids: list[str] = []
    execution_ids: list[str] = []
    ordered_cells = sorted(thread.cells, key=lambda item: (int(item.position or 0), item.created_at))

    for index, cell in enumerate(ordered_cells, start=1):
        revision = _latest_revision(cell)
        if revision is None:
            continue
        revision_ids.append(str(revision.id))
        cell_type = str(revision.cell_type or "markdown")
        label = {
            "agent": "Notebook question",
            "markdown": "Notebook explanation",
            "code": "R computation",
            "output": "Notebook output",
            "provenance": "Provenance",
        }.get(cell_type, cell_type.title())
        lines.extend([f"## Cell {index} — {label}", ""])
        if cell_type == "agent":
            lines.extend(_quoted_block(str(revision.content or "")))
        elif cell_type == "code":
            lines.extend(_fenced_block(str(revision.language or "r"), str(revision.content or "")))
            latest_execution = (
                db.query(CellExecution)
                .filter(CellExecution.revision_id == revision.id)
                .order_by(CellExecution.created_at.desc())
                .first()
            )
            if latest_execution is not None:
                execution_ids.append(str(latest_execution.id))
        elif cell_type == "provenance":
            lines.extend(_quoted_block(str(revision.content or "")))
        elif cell_type == "output":
            lines.extend(_fenced_block("text", str(revision.content or "")))
        else:
            lines.extend(str(revision.content or "").rstrip().splitlines() or [""])
        lines.append("")

    lines.extend([
        "## Execution provenance",
        "",
        "| Cell revision | Status | Execution | Input fingerprint | Environment fingerprint |",
        "| --- | --- | --- | --- | --- |",
    ])
    for cell in ordered_cells:
        revision = _latest_revision(cell)
        if revision is None or revision.cell_type != "code":
            continue
        execution = (
            db.query(CellExecution)
            .filter(CellExecution.revision_id == revision.id)
            .order_by(CellExecution.created_at.desc())
            .first()
        )
        if execution is None:
            lines.append(f"| `{revision.id}` | not executed | — | — | — |")
            continue
        lines.append(
            f"| `{revision.id}` | {execution.status} | `{execution.id}` | "
            f"`{execution.input_fingerprint or '—'}` | `{execution.environment_fingerprint or '—'}` |"
        )

    content = "\n".join(lines).rstrip() + "\n"
    metadata = {
        "source_note_thread_id": str(thread.id),
        "source_cell_count": len(ordered_cells),
        "source_revision_ids": revision_ids,
        "execution_ids": execution_ids,
        "exported_at": exported_at.isoformat(),
        "source_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "validation_status": "source-exported",
    }
    return content, metadata


def report_payload(report: Any) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "project_id": str(report.project_id),
        "name": report.name,
        "slug": report.slug,
        "report_type": report.report_type,
        "status": report.status,
        "source_path": report.source_path,
        "rendered_path": report.rendered_path,
        "metadata": report.report_metadata,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


__all__ = ["build_note_qmd", "report_payload", "safe_report_slug"]
