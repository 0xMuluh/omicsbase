"""Read-only API for the curated QMD Bioconductor knowledge index."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_tenant
from app.database import get_db
from app.services.bioc_knowledge import knowledge_status, search_bioc_knowledge

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/status")
def get_knowledge_status(
    db: Session = Depends(get_db),
    _tenant_id: str = Depends(get_current_tenant),
):
    """Return current book snapshots and recent sync results."""
    return knowledge_status(db)


@router.get("/search")
def search_knowledge(
    q: str = Query(min_length=1, max_length=20_000),
    channel: str = Query(default="stable", pattern="^(stable|preview)$"),
    limit: int = Query(default=6, ge=1, le=20),
    book: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    _tenant_id: str = Depends(get_current_tenant),
):
    """Search QMD-derived prose and code with source citations."""
    return search_bioc_knowledge(db, q, channel=channel, limit=limit, source_slug=book)

