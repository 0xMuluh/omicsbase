"""Catalog synchronization orchestration."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from app.models.knowledge import BiocKnowledgeSyncRun
from app.services.bioc.catalog import load_catalog, source_from_config
from app.services.bioc.indexing import index_snapshot
from app.services.bioc.repository import materialise_repository
from app.services.bioc_qmd import _now

logger = logging.getLogger(__name__)
SUPPORTED_CHANNELS = {"stable", "preview"}


def sync_catalog(
    db: Session,
    catalog_path: str | Path,
    *,
    storage_root: str | Path = "./knowledge",
    channels: Iterable[str] = ("stable", "preview"),
    only_slug: str | None = None,
    load_catalog_fn: Callable[[str | Path], list[dict[str, Any]]] | None = None,
    source_from_config_fn: Callable[..., Any] | None = None,
    materialise_repository_fn: Callable[..., Any] | None = None,
    index_snapshot_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Synchronise configured QMD books into immutable database snapshots."""
    storage = Path(storage_root).expanduser().resolve()
    storage.mkdir(parents=True, exist_ok=True)
    entries = (load_catalog_fn or load_catalog)(catalog_path)
    source_upsert = source_from_config_fn or source_from_config
    materialise = materialise_repository_fn or materialise_repository
    index = index_snapshot_fn or index_snapshot
    summary = {"status": "ok", "sources": 0, "snapshots": 0, "documents": 0, "chunks": 0, "errors": []}

    for entry in entries:
        if only_slug and str(entry.get("slug")) != only_slug:
            continue
        source = source_upsert(db, entry)
        if not source.enabled:
            continue
        summary["sources"] += 1
        for channel in channels:
            if channel not in SUPPORTED_CHANNELS:
                raise ValueError(f"Unsupported Bioconductor knowledge channel: {channel}")
            run = BiocKnowledgeSyncRun(
                id=str(uuid.uuid4()),
                source_id=source.id,
                channel=channel,
                status="running",
                started_at=_now(),
            )
            db.add(run)
            db.flush()
            try:
                root, snapshot_key, commit_sha, resolved_ref = materialise(entry, channel, storage)
                configured_ref = str(entry.get("stable_ref" if channel == "stable" else "preview_ref") or channel)
                run.requested_ref = resolved_ref
                run.resolved_ref = commit_sha or snapshot_key
                run.run_metadata = {
                    "configured_ref": configured_ref,
                    "resolved_ref": resolved_ref,
                    "repository_url": source.repository_url,
                }
                snapshot = index(
                    db,
                    source=source,
                    root=root,
                    channel=channel,
                    requested_ref=run.requested_ref,
                    snapshot_key=snapshot_key,
                    commit_sha=commit_sha,
                    source_url=source.book_url,
                    repository_url=source.repository_url,
                )
                run.status = "completed"
                run.finished_at = _now()
                run.files_seen = snapshot.document_count
                run.documents_indexed = snapshot.document_count
                run.chunks_indexed = snapshot.chunk_count
                summary["snapshots"] += 1
                summary["documents"] += snapshot.document_count
                summary["chunks"] += snapshot.chunk_count
            except Exception as exc:
                error_text = str(exc)[:4000]
                # A failed source must not poison the session for the next
                # book/channel. Recreate the audit row after rolling back the
                # failed indexing transaction.
                db.rollback()
                source = source_upsert(db, entry)
                run = BiocKnowledgeSyncRun(
                    id=str(uuid.uuid4()),
                    source_id=source.id,
                    channel=channel,
                    status="failed",
                    requested_ref=str(entry.get("stable_ref" if channel == "stable" else "preview_ref") or channel),
                    error=error_text,
                    started_at=_now(),
                    finished_at=_now(),
                )
                db.add(run)
                summary["errors"].append({"slug": source.slug, "channel": channel, "error": error_text})
                logger.exception("Bioconductor knowledge sync failed for %s/%s", source.slug, channel)
            db.commit()

    if summary["errors"]:
        summary["status"] = "partial"
    return summary


__all__ = ["sync_catalog"]
