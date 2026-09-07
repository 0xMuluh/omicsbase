"""Index health and synchronization status for Bioconductor knowledge."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.knowledge import (
    BiocBookSnapshot,
    BiocBookSource,
    BiocKnowledgeEmbedding,
    BiocKnowledgeSyncRun,
)
from app.services.bioc.retrieval import semantic_candidate_limit

INDEX_VERSION = 1


def knowledge_status(db: Session) -> dict[str, Any]:
    sources = db.query(BiocBookSource).order_by(BiocBookSource.title.asc()).all()
    snapshots = db.query(BiocBookSnapshot).order_by(BiocBookSnapshot.updated_at.desc()).all()
    runs = db.query(BiocKnowledgeSyncRun).order_by(BiocKnowledgeSyncRun.started_at.desc()).limit(20).all()
    semantic_model = str(getattr(settings, "bioc_knowledge_embedding_model", "") or "").strip()
    semantic_embeddings = (
        db.query(func.count(BiocKnowledgeEmbedding.id))
        .filter(BiocKnowledgeEmbedding.model_name == semantic_model)
        .scalar()
        if semantic_model
        else 0
    )
    return {
        "index_version": INDEX_VERSION,
        "semantic_retrieval": {
            "enabled": bool(getattr(settings, "bioc_knowledge_semantic_enabled", True)),
            "model": semantic_model or None,
            "embedding_count": int(semantic_embeddings or 0),
            "candidate_limit": semantic_candidate_limit(6),
        },
        "sources": [
            {
                "id": str(source.id),
                "slug": source.slug,
                "stable_ref": source.stable_ref,
                "preview_ref": source.preview_ref,
                "title": source.title,
                "enabled": source.enabled,
                "book_url": source.book_url,
                "repository_url": source.repository_url,
                "license": source.license,
            }
            for source in sources
        ],
        "snapshots": [
            {
                "id": str(snapshot.id),
                "source_id": str(snapshot.source_id),
                "channel": snapshot.channel,
                "ref": snapshot.requested_ref,
                "commit_sha": snapshot.commit_sha,
                "status": snapshot.status,
                "document_count": snapshot.document_count,
                "chunk_count": snapshot.chunk_count,
                "updated_at": snapshot.updated_at,
            }
            for snapshot in snapshots
        ],
        "recent_syncs": [
            {
                "id": str(run.id),
                "source_id": str(run.source_id),
                "channel": run.channel,
                "status": run.status,
                "requested_ref": run.requested_ref,
                "resolved_ref": run.resolved_ref,
                "documents_indexed": run.documents_indexed,
                "chunks_indexed": run.chunks_indexed,
                "error": run.error,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in runs
        ],
    }


__all__ = ["knowledge_status"]
