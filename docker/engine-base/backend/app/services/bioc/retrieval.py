"""Lexical, semantic, and hybrid retrieval over indexed QMD chunks."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sqlalchemy import func, or_
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
from app.services.bioc_embeddings import EmbeddingUnavailable, cosine_similarity, embed_texts, unpack_embedding
from app.services.bioc_qmd import _tokens

logger = logging.getLogger(__name__)

MAX_SEARCH_CHARS = 4_000
DEFAULT_CHANNEL = "stable"
SUPPORTED_CHANNELS = {"stable", "preview"}
SEARCH_CACHE_TTL_SECONDS = 300
RRF_K = 60.0
_SEARCH_CACHE: dict[
    tuple[str, str, int, str | None, bool, str],
    tuple[float, list[dict[str, Any]], dict[str, Any] | None, str],
] = {}


def semantic_candidate_limit(limit: int) -> int:
    configured = int(getattr(settings, "bioc_knowledge_semantic_candidate_limit", 64) or 64)
    return max(limit, min(200, max(16, configured)))


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user query terms match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def lexical_ranked(
    db: Session,
    snapshot_ids: list[str],
    query: str,
    query_terms: set[str],
    candidate_limit: int,
) -> list[tuple[float, tuple[Any, Any, Any, Any]]]:
    """Return the deterministic lexical ranking."""
    if not query_terms:
        return []
    df_rows = (
        db.query(BiocKnowledgeTermDf.term, func.sum(BiocKnowledgeTermDf.doc_count))
        .filter(
            BiocKnowledgeTermDf.snapshot_id.in_(snapshot_ids),
            BiocKnowledgeTermDf.term.in_(query_terms),
        )
        .group_by(BiocKnowledgeTermDf.term)
        .all()
    )
    document_frequency: dict[str, int] = {term: int(total) for term, total in df_rows}
    if document_frequency:
        conditions = [
            BiocKnowledgeChunk.search_text.ilike(f"%{escape_like(term)}%", escape="\\")
            for term in query_terms
        ]
        candidate_ids = {
            chunk_id
            for (chunk_id,) in db.query(BiocKnowledgeChunk.id)
            .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
            .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
            .filter(BiocBookSnapshot.id.in_(snapshot_ids), or_(*conditions))
            .all()
        }
    else:
        # Fallback for snapshots indexed before the term-frequency backfill.
        narrow_rows = (
            db.query(BiocKnowledgeChunk.id, BiocKnowledgeChunk.search_text)
            .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
            .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
            .filter(BiocBookSnapshot.id.in_(snapshot_ids))
            .all()
        )
        document_frequency = {}
        candidate_ids = set()
        for chunk_id, search_text in narrow_rows:
            terms = set((search_text or "").split())
            for term in terms:
                document_frequency[term] = document_frequency.get(term, 0) + 1
            if query_terms & terms:
                candidate_ids.add(chunk_id)

    if not candidate_ids:
        return []

    chunk_rows = (
        db.query(BiocKnowledgeChunk, BiocBookDocument, BiocBookSnapshot, BiocBookSource)
        .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
        .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
        .join(BiocBookSource, BiocBookSnapshot.source_id == BiocBookSource.id)
        .filter(BiocKnowledgeChunk.id.in_(candidate_ids))
        .all()
    )
    ranked: list[tuple[float, tuple[Any, Any, Any, Any]]] = []
    for row in chunk_rows:
        chunk, document, snapshot, source = row
        terms = set((chunk.search_text or "").split())
        overlap = query_terms & terms
        if not overlap:
            continue
        score = 0.0
        for term in overlap:
            score += 1.0 + (1.0 / max(1, document_frequency.get(term, 1)))
        searchable = f"{document.title} {'/'.join(chunk.heading_path or [])} {chunk.content}".lower()
        if query.lower() in searchable:
            score += 6.0
        if chunk.code:
            score += 0.25
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1][1].relative_path, item[1][0].ordinal))
    return ranked[:candidate_limit]


def semantic_ranked(
    db: Session,
    snapshots: list[BiocBookSnapshot],
    query: str,
    candidate_limit: int,
    *,
    embedder: Callable[..., list[list[float]]] | None = None,
) -> tuple[list[tuple[float, tuple[Any, Any, Any, Any]]], bool]:
    """Return local semantic candidates and whether the index was available."""
    if not bool(getattr(settings, "bioc_knowledge_semantic_enabled", True)):
        return [], False
    model_name = str(getattr(settings, "bioc_knowledge_embedding_model", "") or "").strip()
    if not model_name or not snapshots:
        return [], False
    snapshot_ids = [str(snapshot.id) for snapshot in snapshots]
    has_embeddings = (
        db.query(BiocKnowledgeEmbedding.id)
        .join(BiocKnowledgeChunk, BiocKnowledgeEmbedding.chunk_id == BiocKnowledgeChunk.id)
        .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
        .filter(
            BiocBookDocument.snapshot_id.in_(snapshot_ids),
            BiocKnowledgeEmbedding.model_name == model_name,
        )
        .first()
    )
    if has_embeddings is None:
        # Do not download/load an embedding model for an index that has not
        # been backfilled yet. Lexical retrieval remains immediately usable.
        return [], False
    try:
        query_vector = (embedder or embed_texts)([query], model_name=model_name, batch_size=1)[0]
    except (EmbeddingUnavailable, IndexError) as exc:
        logger.debug("Semantic knowledge query unavailable: %s", exc)
        return [], False
    except Exception as exc:
        logger.warning("Semantic knowledge query failed; using lexical retrieval: %s", exc)
        return [], False

    rows = (
        db.query(
            BiocKnowledgeEmbedding,
            BiocKnowledgeChunk,
            BiocBookDocument,
            BiocBookSnapshot,
            BiocBookSource,
        )
        .join(BiocKnowledgeChunk, BiocKnowledgeEmbedding.chunk_id == BiocKnowledgeChunk.id)
        .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
        .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
        .join(BiocBookSource, BiocBookSnapshot.source_id == BiocBookSource.id)
        .filter(
            BiocBookSnapshot.id.in_(snapshot_ids),
            BiocKnowledgeEmbedding.model_name == model_name,
        )
        .all()
    )
    ranked: list[tuple[float, tuple[Any, Any, Any, Any]]] = []
    for embedding, chunk, document, snapshot, source in rows:
        vector = unpack_embedding(embedding.vector, int(embedding.dimension or 0))
        if vector is None:
            continue
        score = cosine_similarity(query_vector, vector)
        ranked.append((score, (chunk, document, snapshot, source)))
    ranked.sort(key=lambda item: (-item[0], item[1][1].relative_path, item[1][0].ordinal))
    return ranked[:candidate_limit], bool(ranked)


def fuse_ranked(
    lexical: list[tuple[float, tuple[Any, Any, Any, Any]]],
    semantic: list[tuple[float, tuple[Any, Any, Any, Any]]],
    limit: int,
) -> list[tuple[float, tuple[Any, Any, Any, Any], float | None, float | None, str]]:
    """Fuse complementary rankings without putting either score on a false scale."""
    rows: dict[str, tuple[Any, Any, Any, Any]] = {}
    fused: dict[str, float] = {}
    lexical_scores: dict[str, float] = {}
    semantic_scores: dict[str, float] = {}

    for rank, (score, row) in enumerate(lexical, start=1):
        key = str(row[0].id)
        rows[key] = row
        lexical_scores[key] = score
        fused[key] = fused.get(key, 0.0) + 0.45 / (RRF_K + rank)

    for rank, (score, row) in enumerate(semantic, start=1):
        key = str(row[0].id)
        rows[key] = row
        semantic_scores[key] = score
        fused[key] = fused.get(key, 0.0) + 0.55 / (RRF_K + rank)

    ordered = sorted(rows, key=lambda key: (-fused[key], rows[key][1].relative_path, rows[key][0].ordinal))
    result = []
    for key in ordered[:limit]:
        has_lexical = key in lexical_scores
        has_semantic = key in semantic_scores
        method = "hybrid" if has_lexical and has_semantic else ("semantic" if has_semantic else "lexical")
        result.append((fused[key], rows[key], lexical_scores.get(key), semantic_scores.get(key), method))
    return result


def citation(source: BiocBookSource, snapshot: BiocBookSnapshot, document: BiocBookDocument, chunk: BiocKnowledgeChunk) -> str:
    heading = " > ".join(chunk.heading_path or [document.title])
    return f"{source.title}, {heading} ({snapshot.requested_ref}, {snapshot.commit_sha[:12]})"


def snapshot_label(snapshots: list[BiocBookSnapshot]) -> dict[str, Any] | None:
    if not snapshots:
        return None
    return {
        "channel": snapshots[0].channel,
        "books": [
            {
                "source_id": str(snapshot.source_id),
                "commit_sha": snapshot.commit_sha,
                "ref": snapshot.requested_ref,
                "status": snapshot.status,
            }
            for snapshot in snapshots
        ],
    }


def search_bioc_knowledge(
    db: Session,
    query: str,
    *,
    channel: str = DEFAULT_CHANNEL,
    limit: int = 6,
    source_slug: str | None = None,
    book: str | None = None,
    lexical_ranked_fn: Callable[..., list] | None = None,
    semantic_ranked_fn: Callable[..., tuple[list, bool]] | None = None,
    fuse_ranked_fn: Callable[..., list] | None = None,
    embedder: Callable[..., list[list[float]]] | None = None,
) -> dict[str, Any]:
    """Return cited QMD prose/code recipes using hybrid local retrieval."""
    source_slug = source_slug or book
    query = " ".join(str(query or "").split())
    if not query:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}
    if channel not in SUPPORTED_CHANNELS:
        return {"status": "error", "error": f"Unsupported channel: {channel}"}

    limit = max(1, min(limit, 20))
    semantic_enabled = bool(getattr(settings, "bioc_knowledge_semantic_enabled", True))
    model_name = str(getattr(settings, "bioc_knowledge_embedding_model", "") or "").strip()
    cache_key = (query, channel, limit, source_slug, semantic_enabled, model_name)
    now = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return {
            "status": "ok",
            "query": query,
            "matches": cached[1],
            "knowledge_snapshot": cached[2],
            "cached": True,
            "retrieval_method": cached[3],
            "retrieval_policy": "Local semantic retrieval is fused with exact lexical matching; QMD source excerpts are methodological guidance and executed project results remain authoritative.",
        }

    snapshots_query = db.query(BiocBookSnapshot).filter(
        BiocBookSnapshot.status == ("published" if channel == "stable" else "preview"),
        BiocBookSnapshot.channel == channel,
    )
    if source_slug:
        snapshots_query = snapshots_query.join(BiocBookSource).filter(BiocBookSource.slug == source_slug)
    snapshots = snapshots_query.all()
    snapshot_ids = [snapshot.id for snapshot in snapshots]
    if not snapshot_ids:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}

    candidate_limit = semantic_candidate_limit(limit)
    query_terms = set(_tokens(query))
    lexical = (lexical_ranked_fn or lexical_ranked)(db, snapshot_ids, query, query_terms, candidate_limit)
    semantic, semantic_available = (semantic_ranked_fn or semantic_ranked)(
        db, snapshots, query, candidate_limit, embedder=embedder
    )
    fused = (fuse_ranked_fn or fuse_ranked)(lexical, semantic, limit)
    if not fused:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}

    matches = []
    for fused_score, (chunk, document, snapshot, source), lexical_score, semantic_score, retrieval_method in fused:
        matches.append(
            {
                "score": round(fused_score, 6),
                "lexical_score": round(lexical_score, 6) if lexical_score is not None else None,
                "semantic_score": round(semantic_score, 6) if semantic_score is not None else None,
                "retrieval_method": retrieval_method,
                "book_slug": source.slug,
                "book_title": source.title,
                "channel": snapshot.channel,
                "bioconductor_ref": snapshot.requested_ref,
                "commit_sha": snapshot.commit_sha,
                "document": document.relative_path,
                "title": document.title,
                "heading_path": chunk.heading_path or [],
                "chunk_type": chunk.chunk_type,
                "prose": (chunk.prose or "")[:MAX_SEARCH_CHARS],
                "code": (chunk.code or "")[:MAX_SEARCH_CHARS],
                "code_language": chunk.code_language,
                "source_start_line": chunk.source_start_line,
                "source_end_line": chunk.source_end_line,
                "source_url": document.source_url,
                "citation": citation(source, snapshot, document, chunk),
            }
        )
    retrieval_method = "hybrid" if semantic_available and lexical else ("semantic" if semantic_available else "lexical")
    snapshot_info = snapshot_label(snapshots)
    if not semantic_enabled or semantic_available:
        _SEARCH_CACHE[cache_key] = (now, matches, snapshot_info, retrieval_method)
        if len(_SEARCH_CACHE) > 128:
            _SEARCH_CACHE.clear()
    return {
        "status": "ok",
        "query": query,
        "matches": matches,
        "knowledge_snapshot": snapshot_info,
        "retrieval_method": retrieval_method,
        "retrieval_policy": "Local semantic retrieval is fused with exact lexical matching; QMD source excerpts are methodological guidance and executed project results remain authoritative.",
    }


__all__ = [
    "citation",
    "escape_like",
    "fuse_ranked",
    "lexical_ranked",
    "search_bioc_knowledge",
    "semantic_candidate_limit",
    "semantic_ranked",
    "snapshot_label",
]
