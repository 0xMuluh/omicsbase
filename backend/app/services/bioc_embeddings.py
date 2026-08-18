"""Local embedding helpers for the curated Bioconductor knowledge index.

The optional FastEmbed dependency is loaded lazily. This keeps the lexical
index usable in minimal installations and makes semantic retrieval an
improvement rather than a new hard dependency for every backend operation.
"""

from __future__ import annotations

import math
import threading
from array import array
from pathlib import Path
from typing import Iterable

from app.config import settings

_MODEL_CACHE: dict[tuple[str, str], object] = {}
_MODEL_LOCK = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    """Raised when the configured local embedding runtime cannot be used."""


def _model_cache_dir() -> Path:
    configured = str(getattr(settings, "bioc_knowledge_embedding_cache_dir", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(settings.bioc_knowledge_storage_dir).expanduser().resolve() / "embedding-models"


def _get_model(model_name: str) -> object:
    cache_key = (model_name, str(_model_cache_dir()))
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "FastEmbed is not installed; using lexical book retrieval"
            ) from exc
        try:
            cache_dir = _model_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        except Exception as exc:  # model download/configuration failure
            raise EmbeddingUnavailable(
                f"Could not load local embedding model {model_name!r}: {exc}"
            ) from exc
        _MODEL_CACHE[cache_key] = model
        return model


def _normalise(values: Iterable[float]) -> list[float]:
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise EmbeddingUnavailable("Embedding model returned a zero vector")
    return [value / norm for value in vector]


def embed_texts(
    texts: Iterable[str],
    *,
    model_name: str | None = None,
    batch_size: int | None = None,
) -> list[list[float]]:
    """Embed text locally and return normalized vectors.

    The model is loaded only on the first indexing/search call. This function
    deliberately accepts plain text so tests and future local runtimes can
    replace FastEmbed without changing knowledge retrieval.
    """

    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    name = str(model_name or settings.bioc_knowledge_embedding_model).strip()
    if not name:
        raise EmbeddingUnavailable("No local embedding model is configured")
    model = _get_model(name)
    kwargs = {}
    if batch_size:
        kwargs["batch_size"] = max(1, int(batch_size))
    try:
        raw_vectors = model.embed(values, **kwargs)
        vectors = [_normalise(vector) for vector in raw_vectors]
    except EmbeddingUnavailable:
        raise
    except Exception as exc:
        raise EmbeddingUnavailable(f"Local embedding failed: {exc}") from exc
    if len(vectors) != len(values):
        raise EmbeddingUnavailable("Embedding model returned an unexpected vector count")
    return vectors


def pack_embedding(vector: Iterable[float]) -> tuple[bytes, int]:
    """Pack a normalized vector into compact, portable float32 storage."""

    values = [float(value) for value in vector]
    if not values:
        raise ValueError("Cannot store an empty embedding")
    return array("f", values).tobytes(), len(values)


def unpack_embedding(payload: bytes, dimension: int) -> list[float] | None:
    """Decode one stored vector, rejecting corrupt or incompatible payloads."""

    if not payload or dimension <= 0:
        return None
    values = array("f")
    try:
        values.frombytes(bytes(payload))
    except (TypeError, ValueError, OverflowError):
        return None
    if len(values) != int(dimension):
        return None
    return list(values)


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    """Score normalized vectors without requiring a vector database."""

    left_values = list(left)
    right_values = list(right)
    if len(left_values) != len(right_values) or not left_values:
        return -1.0
    return sum(a * b for a, b in zip(left_values, right_values))


__all__ = [
    "EmbeddingUnavailable",
    "embed_texts",
    "pack_embedding",
    "unpack_embedding",
    "cosine_similarity",
]
