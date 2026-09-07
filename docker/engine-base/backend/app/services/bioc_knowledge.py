"""Compatibility façade for the Bioconductor QMD knowledge services.

The public imports remain stable for API routes, tasks, and existing callers.
Domain implementation now lives under :mod:`app.services.bioc`:
catalog loading, repository materialisation, synchronization, snapshot
indexing, retrieval, and status are separate modules.  The small private
wrappers intentionally preserve the old monkeypatch/import seams used by
integrations and tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.services.bioc import catalog, indexing, repository, retrieval, status, sync
from app.services.bioc_embeddings import embed_texts
from app.services.bioc_qmd import (
    QmdBlock,
    QmdDocument,
    iter_qmd_files,
    parse_qmd,
)

MAX_SEARCH_CHARS = retrieval.MAX_SEARCH_CHARS
INDEX_VERSION = status.INDEX_VERSION
DEFAULT_CHANNEL = retrieval.DEFAULT_CHANNEL
SUPPORTED_CHANNELS = retrieval.SUPPORTED_CHANNELS
SEARCH_CACHE_TTL_SECONDS = retrieval.SEARCH_CACHE_TTL_SECONDS
RRF_K = retrieval.RRF_K
_SEARCH_CACHE = retrieval._SEARCH_CACHE


def _tree_fingerprint(root: Path, paths: list[Path]) -> str:
    return repository.tree_fingerprint(root, paths)


def _safe_json(value: Any) -> Any:
    return catalog.safe_json(value)


def load_catalog(path: str | Path) -> list[dict[str, Any]]:
    return catalog.load_catalog(path)


def _source_from_config(db: Session, entry: dict[str, Any]):
    return catalog.source_from_config(db, entry)


def _git_command(command: list[str], *, timeout: int = 600) -> str:
    return repository.git_command(command, timeout=timeout)


def _remote_ref_names(repository_url: str, kind: str) -> list[str]:
    return repository.remote_ref_names(repository_url, kind, git_command_fn=_git_command)


def _default_remote_branch(repository_url: str) -> str | None:
    return repository.default_remote_branch(repository_url, git_command_fn=_git_command)


def _version_key(value: str) -> tuple[int, ...] | None:
    return repository.version_key(value)


def _resolve_repository_reference(repository_url: str, requested: str, channel: str) -> str:
    return repository.resolve_repository_reference(
        repository_url,
        requested,
        channel,
        remote_ref_names_fn=_remote_ref_names,
        default_remote_branch_fn=_default_remote_branch,
    )


def _materialise_repository(
    entry: dict[str, Any],
    channel: str,
    storage_root: Path,
) -> tuple[Path, str, str | None, str]:
    return repository.materialise_repository(
        entry,
        channel,
        storage_root,
        resolve_reference_fn=_resolve_repository_reference,
        git_command_fn=_git_command,
        tree_fingerprint_fn=_tree_fingerprint,
    )


def _embedding_status(
    db: Session,
    snapshot: Any,
    chunks: list[Any] | None = None,
) -> dict[str, Any]:
    return indexing.embedding_status(db, snapshot, chunks, embedder=embed_texts)


def _source_file_url(
    book_url: str | None,
    repository_url: str | None,
    channel: str,
    commit_sha: str | None,
    relative_path: str,
) -> str | None:
    return indexing.source_file_url(book_url, repository_url, channel, commit_sha, relative_path)


def _chunk_embedding_text(chunk: Any) -> str:
    return indexing.chunk_embedding_text(chunk)


def _supersede_previous_snapshots(db: Session, snapshot: Any) -> None:
    return indexing.supersede_previous_snapshots(db, snapshot)


def _index_snapshot(db: Session, **kwargs: Any):
    return indexing.index_snapshot(
        db,
        embedding_status_fn=_embedding_status,
        safe_json_fn=_safe_json,
        source_file_url_fn=_source_file_url,
        **kwargs,
    )


def sync_catalog(
    db: Session,
    catalog_path: str | Path,
    *,
    storage_root: str | Path = "./knowledge",
    channels: Iterable[str] = ("stable", "preview"),
    only_slug: str | None = None,
) -> dict[str, Any]:
    return sync.sync_catalog(
        db,
        catalog_path,
        storage_root=storage_root,
        channels=channels,
        only_slug=only_slug,
        load_catalog_fn=load_catalog,
        source_from_config_fn=_source_from_config,
        materialise_repository_fn=_materialise_repository,
        index_snapshot_fn=_index_snapshot,
    )


def _semantic_candidate_limit(limit: int) -> int:
    return retrieval.semantic_candidate_limit(limit)


def _lexical_ranked(
    db: Session,
    snapshot_ids: list[str],
    query: str,
    query_terms: set[str],
    candidate_limit: int,
):
    return retrieval.lexical_ranked(db, snapshot_ids, query, query_terms, candidate_limit)


def _semantic_ranked(
    db: Session,
    snapshots: list[Any],
    query: str,
    candidate_limit: int,
    *,
    embedder: Any = None,
):
    return retrieval.semantic_ranked(
        db,
        snapshots,
        query,
        candidate_limit,
        embedder=embedder or embed_texts,
    )


def _fuse_ranked(lexical: list, semantic: list, limit: int):
    return retrieval.fuse_ranked(lexical, semantic, limit)


def _citation(source: Any, snapshot: Any, document: Any, chunk: Any) -> str:
    return retrieval.citation(source, snapshot, document, chunk)


def _escape_like(value: str) -> str:
    return retrieval.escape_like(value)


def _snapshot_label(snapshots: list[Any]) -> dict[str, Any] | None:
    return retrieval.snapshot_label(snapshots)


def search_bioc_knowledge(
    db: Session,
    query: str,
    *,
    channel: str = DEFAULT_CHANNEL,
    limit: int = 6,
    source_slug: str | None = None,
    book: str | None = None,
) -> dict[str, Any]:
    return retrieval.search_bioc_knowledge(
        db,
        query,
        channel=channel,
        limit=limit,
        source_slug=source_slug,
        book=book,
        lexical_ranked_fn=_lexical_ranked,
        semantic_ranked_fn=_semantic_ranked,
        fuse_ranked_fn=_fuse_ranked,
        embedder=embed_texts,
    )


def knowledge_status(db: Session) -> dict[str, Any]:
    return status.knowledge_status(db)


__all__ = [
    "QmdBlock",
    "QmdDocument",
    "parse_qmd",
    "iter_qmd_files",
    "load_catalog",
    "sync_catalog",
    "search_bioc_knowledge",
    "knowledge_status",
]
