"""Builds an embedded SQLite FTS5 knowledge index from curated Bioconductor books."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict

from engine.knowledge.qmd_parser import iter_qmd_files, parse_qmd

BOOKS = [
    {"slug": "osca", "title": "Orchestrating Single-Cell Analysis with Bioconductor"},
    {"slug": "osta", "title": "Orchestrating Spatial Transcriptomics Analysis with Bioconductor"},
    {"slug": "oma", "title": "Orchestrating Microbiome Analysis with Bioconductor"},
    {"slug": "r-for-mass-spectrometry", "title": "R for Mass Spectrometry"},
    {"slug": "metabonaut", "title": "Metabonaut (Metabolomics)"},
]

def build_index(repos_dir: str | Path, db_path: str | Path) -> Dict[str, int]:
    repos_path = Path(repos_dir).resolve()
    db_file = Path(db_path).resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    if db_file.exists():
        db_file.unlink()

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    cur.execute("""
    CREATE VIRTUAL TABLE bioc_knowledge USING fts5(
        book_slug,
        book_title,
        chapter_title,
        heading_path,
        chunk_type,
        prose,
        code,
        content,
        source_file,
        tokenize='porter unicode61'
    );
    """)

    stats = {}
    total_chunks = 0

    for book in BOOKS:
        slug = book["slug"]
        title = book["title"]
        book_repo = repos_path / slug / "stable"
        if not book_repo.exists():
            book_repo = repos_path / slug
        if not book_repo.exists():
            print(f"Warning: Repository for {slug} not found at {book_repo}")
            continue

        qmd_files = list(iter_qmd_files(book_repo))
        print(f"Indexing {slug} ({len(qmd_files)} files)...")

        book_chunk_count = 0
        for f in qmd_files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                doc = parse_qmd(text, relative_path=str(f.relative_to(book_repo)))
            except Exception as e:
                print(f"  Error parsing {f.name}: {e}")
                continue

            for block in doc.blocks:
                content = block.content.strip()
                if not content:
                    continue

                heading_str = " > ".join(block.heading_path) if block.heading_path else doc.title
                cur.execute(
                    """
                    INSERT INTO bioc_knowledge (
                        book_slug, book_title, chapter_title, heading_path,
                        chunk_type, prose, code, content, source_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slug,
                        title,
                        doc.title,
                        heading_str,
                        block.chunk_type,
                        block.prose.strip(),
                        block.code.strip(),
                        content,
                        doc.relative_path,
                    )
                )
                book_chunk_count += 1
                total_chunks += 1

        stats[slug] = book_chunk_count
        print(f"  Indexed {book_chunk_count} chunks for {slug}.")

    conn.commit()
    conn.close()
    print(f"Successfully built {db_file} with {total_chunks} total knowledge chunks.")
    return stats

if __name__ == "__main__":
    default_repos = Path(__file__).resolve().parent.parent.parent.parent / "omicsbase" / "backend" / "knowledge" / "repositories"
    default_db = Path(__file__).resolve().parent / "knowledge.db"

    repos_arg = sys.argv[1] if len(sys.argv) > 1 else str(default_repos)
    db_arg = sys.argv[2] if len(sys.argv) > 2 else str(default_db)

    build_index(repos_arg, db_arg)
