"""Tests for QMD-first Bioconductor knowledge ingestion and retrieval."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.knowledge import BiocBookSnapshot
from app.services.bioc_knowledge import parse_qmd, search_bioc_knowledge, sync_catalog


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, session_factory


def test_parse_qmd_keeps_frontmatter_headings_and_code():
    document = parse_qmd(
        """---
title: Differential abundance recipe
bioc: '3.23'
---

# Differential abundance

Use a model that respects the count distribution and the study design.

```{r}
library(edgeR)
fit <- glmQLFit(y, design)
```
""",
        "chapters/da.qmd",
    )

    assert document.title == "Differential abundance recipe"
    assert document.frontmatter["bioc"] == "3.23"
    assert len(document.blocks) == 1
    assert document.blocks[0].heading_path == ["Differential abundance"]
    assert document.blocks[0].code_language == "r"
    assert "glmQLFit" in document.blocks[0].code
    assert document.blocks[0].chunk_type == "mixed"


def test_sync_indexes_qmd_and_returns_cited_code(tmp_path: Path):
    engine, session_factory = _db()
    book = tmp_path / "book"
    (book / "pages").mkdir(parents=True)
    (book / "pages" / "normalization.qmd").write_text(
        """---
title: Normalization
---

# Normalization

Choose normalization after considering library size and experimental design.

```{r}
library(edgeR)
y <- calcNormFactors(y)
```
"""
    )
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        f"""books:\n  - slug: demo\n    title: Demo Bioconductor Book\n    book_url: https://example.test/demo\n    source_path: {book}\n    stable_ref: release\n    preview_ref: devel\n    enabled: true\n"""
    )

    db = session_factory()
    try:
        result = sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))
        assert result["status"] == "ok"
        assert result["documents"] == 1
        assert result["chunks"] == 1

        search = search_bioc_knowledge(db, "normalization library size", channel="stable")
        assert search["status"] == "ok"
        assert search["matches"][0]["book_slug"] == "demo"
        assert search["matches"][0]["code_language"] == "r"
        assert "calcNormFactors" in search["matches"][0]["code"]
        assert "Demo Bioconductor Book" in search["matches"][0]["citation"]
        assert search["matches"][0]["source_url"].endswith("https://example.test/demo")

        # A second unchanged sync reuses the immutable snapshot rather than
        # replacing it, which makes citations stable across weekly runs.
        second = sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))
        assert second["snapshots"] == 1
        assert db.query(BiocBookSnapshot).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_semantic_retrieval_recovers_a_concept_without_term_overlap(monkeypatch, tmp_path: Path):
    from app.config import settings
    from app.models.knowledge import BiocKnowledgeEmbedding

    engine, session_factory = _db()
    book = tmp_path / "book"
    book.mkdir()
    (book / "permanova.qmd").write_text(
        "# PERMANOVA\n\nCompare community centroids with a permutation procedure.\n"
    )
    (book / "normalization.qmd").write_text(
        "# Normalization\n\nAdjust library size before modeling counts.\n"
    )
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(f"books:\n  - slug: demo\n    title: Demo\n    source_path: {book}\n")

    def fake_embed(texts, **_kwargs):
        return [
            [1.0, 0.0] if "semanticzzzz" in text.lower() or "centroid" in text.lower() else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr("app.services.bioc_knowledge.embed_texts", fake_embed)
    monkeypatch.setattr(settings, "bioc_knowledge_semantic_enabled", True)
    monkeypatch.setattr(settings, "bioc_knowledge_embedding_model", "test-model")

    db = session_factory()
    try:
        sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))
        result = search_bioc_knowledge(db, "semanticzzzz", channel="stable", limit=1)

        assert result["retrieval_method"] == "semantic"
        assert result["matches"][0]["heading_path"] == ["PERMANOVA"]
        assert result["matches"][0]["citation"].startswith("Demo, PERMANOVA")
        assert db.query(BiocKnowledgeEmbedding).count() == 2
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

def test_changed_source_creates_new_snapshot_and_supersedes_old(tmp_path: Path):
    engine, session_factory = _db()
    book = tmp_path / "book"
    book.mkdir()
    source = book / "workflow.qmd"
    source.write_text("# First\n\nUse a robust model.\n")
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(f"books:\n  - slug: demo\n    title: Demo\n    source_path: {book}\n")

    db = session_factory()
    try:
        sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))
        source.write_text("# Second\n\nUse a validated model and inspect dispersion.\n")
        sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))

        snapshots = db.query(BiocBookSnapshot).order_by(BiocBookSnapshot.created_at.asc()).all()
        assert len(snapshots) == 2
        assert snapshots[0].status == "superseded"
        assert snapshots[1].status == "published"
        assert search_bioc_knowledge(db, "dispersion", channel="stable")["matches"]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)



def test_stable_reference_aliases_resolve_to_concrete_refs(monkeypatch):
    from app.services import bioc_knowledge

    def fake_refs(_url, kind):
        if kind == "heads":
            return ["devel", "RELEASE_3_22", "RELEASE_3_23", "master"]
        return ["v1.4.0", "v1.5.0"]

    monkeypatch.setattr(bioc_knowledge, "_remote_ref_names", fake_refs)
    assert bioc_knowledge._resolve_repository_reference("https://github.com/example/book", "auto", "stable") == "RELEASE_3_23"
    assert bioc_knowledge._resolve_repository_reference("https://github.com/example/book", "auto", "preview") == "devel"
    assert bioc_knowledge._resolve_repository_reference("https://github.com/example/book", "latest_tag", "stable") == "v1.5.0"


def test_search_bioc_knowledge_accepts_book_alias(tmp_path: Path):
    engine, session_factory = _db()
    book = tmp_path / "book"
    book.mkdir()
    (book / "workflow.qmd").write_text("# Chapter\n\nPerform normalization.\n")
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(f"books:\n  - slug: demo\n    title: Demo\n    source_path: {book}\n")

    db = session_factory()
    try:
        sync_catalog(db, catalog, storage_root=tmp_path / "storage", channels=("stable",))
        result_slug = search_bioc_knowledge(db, "normalization", source_slug="demo")
        result_book = search_bioc_knowledge(db, "normalization", book="demo")
        assert result_slug["status"] == "ok"
        assert result_book["status"] == "ok"
        assert len(result_slug["matches"]) == len(result_book["matches"]) == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

