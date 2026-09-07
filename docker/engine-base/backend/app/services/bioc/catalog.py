"""Catalog loading and source metadata for Bioconductor books."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.models.knowledge import BiocBookSource
from app.services.bioc_qmd import _now


def safe_json(value: Any) -> Any:
    """Convert YAML values to JSON-safe metadata without losing structure."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def load_catalog(path: str | Path) -> list[dict[str, Any]]:
    """Load a curated YAML catalog; empty/missing catalogs are safe no-ops."""
    catalog_path = Path(path)
    if not catalog_path.exists():
        return []
    payload = yaml.safe_load(catalog_path.read_text()) or {}
    entries = payload.get("books", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("Bioconductor knowledge catalog must contain a 'books' list")
    return [entry for entry in entries if isinstance(entry, dict)]


def source_from_config(db: Session, entry: dict[str, Any]) -> BiocBookSource:
    """Upsert one catalog entry into the source registry."""
    slug = str(entry.get("slug") or "").strip()
    title = str(entry.get("title") or slug).strip()
    if not slug:
        raise ValueError("Every Bioconductor book catalog entry needs a slug")
    source = db.query(BiocBookSource).filter(BiocBookSource.slug == slug).one_or_none()
    if source is None:
        source = BiocBookSource(id=str(uuid.uuid4()), slug=slug, title=title)
        db.add(source)
    source.title = title[:255]
    source.description = str(entry.get("description") or "")[:4000] or None
    source.book_url = str(entry.get("book_url") or "")[:1000] or None
    source.repository_url = str(entry.get("repository_url") or "")[:1000] or None
    source.license = str(entry.get("license") or "")[:255] or None
    source.stable_ref = str(entry.get("stable_ref") or "release")[:255]
    source.preview_ref = str(entry.get("preview_ref") or "devel")[:255]
    source.enabled = bool(entry.get("enabled", True))
    source.source_metadata = {
        key: safe_json(value)
        for key, value in entry.items()
        if key not in {
            "slug", "title", "description", "book_url", "repository_url",
            "license", "stable_ref", "preview_ref", "enabled",
        }
    }
    source.updated_at = _now()
    db.flush()
    return source


__all__ = ["load_catalog", "safe_json", "source_from_config"]
