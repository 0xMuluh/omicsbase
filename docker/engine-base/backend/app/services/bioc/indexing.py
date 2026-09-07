"""QMD snapshot indexing and embedding maintenance."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.models.knowledge import (
    BiocBookDocument,
    BiocBookSnapshot,
    BiocBookSource,
    BiocKnowledgeChunk,
    BiocKnowledgeEmbedding,
    BiocKnowledgeTermDf,
)
from app.services.bioc_embeddings import (
    EmbeddingUnavailable,
    embed_texts,
    pack_embedding,
)
from app.services.bioc_qmd import MAX_CHUNK_CHARS, _now, _sha256, _tokens, iter_qmd_files, parse_qmd
from app.services.bioc.catalog import safe_json


def chunk_embedding_text(chunk: BiocKnowledgeChunk) -> str:
    """Build the stable text representation used for semantic retrieval."""
    heading = " > ".join(str(item) for item in (chunk.heading_path or []) if str(item).strip())
    parts = [heading, str(chunk.prose or "").strip()]
    if chunk.code:
        parts.append(f"R code:\n{str(chunk.code).strip()}")
    return "\n\n".join(part for part in parts if part).strip()


def embedding_status(
    db: Session,
    snapshot: BiocBookSnapshot,
    chunks: list[BiocKnowledgeChunk] | None = None,
    *,
    embedder: Callable[..., list[list[float]]] | None = None,
) -> dict[str, Any]:
    """Ensure the configured model has vectors for a published snapshot."""
    model_name = str(getattr(settings, "bioc_knowledge_embedding_model", "") or "").strip()
    if not bool(getattr(settings, "bioc_knowledge_semantic_enabled", True)):
        return {"status": "disabled", "model": model_name or None}
    if not model_name:
        return {"status": "unavailable", "error": "No embedding model configured"}

    if chunks is None:
        chunks = (
            db.query(BiocKnowledgeChunk)
            .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
            .filter(BiocBookDocument.snapshot_id == snapshot.id)
            .order_by(BiocKnowledgeChunk.ordinal.asc())
            .all()
        )
    if not chunks:
        return {"status": "complete", "model": model_name, "embedded": 0, "total": 0}

    chunk_ids = [str(chunk.id) for chunk in chunks]
    existing = (
        db.query(BiocKnowledgeEmbedding)
        .filter(
            BiocKnowledgeEmbedding.model_name == model_name,
            BiocKnowledgeEmbedding.chunk_id.in_(chunk_ids),
        )
        .all()
    )
    existing_by_chunk = {str(item.chunk_id): item for item in existing}
    missing = [
        chunk
        for chunk in chunks
        if (
            str(chunk.id) not in existing_by_chunk
            or existing_by_chunk[str(chunk.id)].content_sha256 != str(chunk.content_sha256)
        )
    ]
    if not missing:
        dimension = existing[0].dimension if existing else None
        return {
            "status": "complete",
            "model": model_name,
            "embedded": len(existing),
            "total": len(chunks),
            "dimension": dimension,
        }

    try:
        vectors = (embedder or embed_texts)(
            [chunk_embedding_text(chunk) for chunk in missing],
            model_name=model_name,
            batch_size=int(getattr(settings, "bioc_knowledge_embedding_batch_size", 32) or 32),
        )
    except EmbeddingUnavailable as exc:
        return {"status": "unavailable", "model": model_name, "error": str(exc)[:500]}
    except Exception as exc:
        return {"status": "unavailable", "model": model_name, "error": str(exc)[:500]}

    if len(vectors) != len(missing):
        return {
            "status": "unavailable",
            "model": model_name,
            "error": "Embedding model returned an unexpected vector count",
        }

    dimensions: set[int] = set()
    for chunk, vector in zip(missing, vectors):
        payload, dimension = pack_embedding(vector)
        dimensions.add(dimension)
        item = existing_by_chunk.get(str(chunk.id))
        if item is None:
            item = BiocKnowledgeEmbedding(chunk_id=str(chunk.id), model_name=model_name)
            db.add(item)
        item.dimension = dimension
        item.vector = payload
        item.content_sha256 = str(chunk.content_sha256)
    db.flush()
    return {
        "status": "complete",
        "model": model_name,
        "embedded": len(chunks),
        "total": len(chunks),
        "dimension": max(dimensions) if dimensions else None,
    }


def source_file_url(
    book_url: str | None,
    repository_url: str | None,
    channel: str,
    commit_sha: str | None,
    relative_path: str,
) -> str | None:
    if repository_url and commit_sha:
        base = repository_url.rstrip("/")
        return f"{base}/blob/{commit_sha}/{relative_path}"
    return book_url


def supersede_previous_snapshots(db: Session, snapshot: BiocBookSnapshot) -> None:
    previous = (
        db.query(BiocBookSnapshot)
        .filter(
            BiocBookSnapshot.source_id == snapshot.source_id,
            BiocBookSnapshot.channel == snapshot.channel,
            BiocBookSnapshot.id != snapshot.id,
            BiocBookSnapshot.status.in_(["published", "preview"]),
        )
        .all()
    )
    for superseded in previous:
        superseded.status = "superseded"
        superseded.updated_at = _now()
        db.query(BiocKnowledgeTermDf).filter(
            BiocKnowledgeTermDf.snapshot_id == superseded.id
        ).delete(synchronize_session=False)


def index_snapshot(
    db: Session,
    *,
    source: BiocBookSource,
    root: Path,
    channel: str,
    requested_ref: str,
    snapshot_key: str,
    commit_sha: str | None,
    source_url: str | None,
    repository_url: str | None,
    embedding_status_fn: Callable[..., dict[str, Any]] | None = None,
    safe_json_fn: Callable[[Any], Any] | None = None,
    source_file_url_fn: Callable[..., str | None] | None = None,
) -> BiocBookSnapshot:
    """Index one immutable QMD source snapshot."""
    existing = (
        db.query(BiocBookSnapshot)
        .filter(
            BiocBookSnapshot.source_id == source.id,
            BiocBookSnapshot.channel == channel,
            BiocBookSnapshot.snapshot_key == snapshot_key,
        )
        .one_or_none()
    )
    status_embeddings = embedding_status_fn or embedding_status
    json_safe = safe_json_fn or safe_json
    file_url = source_file_url_fn or source_file_url
    if existing is not None and existing.status in {"published", "preview"}:
        if existing.requested_ref != requested_ref:
            existing.requested_ref = requested_ref[:255]
            existing.updated_at = _now()
            db.flush()
        embedding_metadata = status_embeddings(db, existing)
        snapshot_metadata = dict(existing.snapshot_metadata or {})
        snapshot_metadata["semantic_embedding"] = embedding_metadata
        existing.snapshot_metadata = snapshot_metadata
        return existing

    if existing is None:
        snapshot = BiocBookSnapshot(
            id=str(uuid.uuid4()),
            source_id=source.id,
            channel=channel,
            requested_ref=requested_ref[:255],
            snapshot_key=snapshot_key[:128],
            commit_sha=(commit_sha or snapshot_key)[:128],
            status="staged",
            snapshot_metadata={"index_version": 1},
        )
        db.add(snapshot)
        db.flush()
    else:
        snapshot = existing
        snapshot.status = "staged"
        snapshot.requested_ref = requested_ref[:255]
        snapshot.commit_sha = (commit_sha or snapshot_key)[:128]
        db.query(BiocBookDocument).filter(BiocBookDocument.snapshot_id == snapshot.id).delete()

    document_count = 0
    chunk_count = 0
    term_doc_counts: Counter[str] = Counter()
    indexed_chunks: list[BiocKnowledgeChunk] = []
    for path in list(iter_qmd_files(root)):
        relative_path = path.relative_to(root).as_posix()
        parsed = parse_qmd(path.read_text(errors="replace"), relative_path)
        document = BiocBookDocument(
            id=str(uuid.uuid4()),
            snapshot_id=snapshot.id,
            relative_path=relative_path,
            title=parsed.title,
            content_sha256=parsed.content_sha256,
            frontmatter=json_safe(parsed.frontmatter),
            source_url=file_url(source_url, repository_url, channel, snapshot.commit_sha, relative_path),
        )
        db.add(document)
        db.flush()
        document_count += 1
        for block in parsed.blocks:
            if not block.content.strip():
                continue
            search_text = " ".join(_tokens(f"{' '.join(block.heading_path)} {block.content}"))
            for term in set(search_text.split()):
                term_doc_counts[term] += 1
            chunk = BiocKnowledgeChunk(
                id=str(uuid.uuid4()),
                document_id=document.id,
                ordinal=block.ordinal,
                chunk_type=block.chunk_type,
                heading_path=block.heading_path,
                prose=block.prose[:MAX_CHUNK_CHARS],
                code=block.code[:MAX_CHUNK_CHARS] or None,
                code_language=block.code_language,
                content=block.content[:MAX_CHUNK_CHARS],
                search_text=search_text,
                content_sha256=_sha256(block.content),
                source_start_line=block.source_start_line,
                source_end_line=block.source_end_line,
                chunk_metadata=json_safe(block.metadata),
            )
            db.add(chunk)
            indexed_chunks.append(chunk)
            chunk_count += 1
    db.query(BiocKnowledgeTermDf).filter(BiocKnowledgeTermDf.snapshot_id == snapshot.id).delete()
    for term, doc_count in term_doc_counts.items():
        db.add(BiocKnowledgeTermDf(snapshot_id=snapshot.id, term=term[:128], doc_count=doc_count))
    snapshot.document_count = document_count
    snapshot.chunk_count = chunk_count
    snapshot.status = "published" if channel == "stable" else "preview"
    snapshot.published_at = _now()
    snapshot.updated_at = _now()
    db.flush()
    embedding_metadata = status_embeddings(db, snapshot, indexed_chunks)
    snapshot_metadata = dict(snapshot.snapshot_metadata or {})
    snapshot_metadata["semantic_embedding"] = embedding_metadata
    snapshot.snapshot_metadata = snapshot_metadata
    db.flush()
    supersede_previous_snapshots(db, snapshot)
    return snapshot


__all__ = [
    "chunk_embedding_text",
    "embedding_status",
    "index_snapshot",
    "source_file_url",
    "supersede_previous_snapshots",
]
