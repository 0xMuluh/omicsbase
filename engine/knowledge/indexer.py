"""Builds an embedded SQLite FTS5 knowledge index from curated Bioconductor books."""

from __future__ import annotations

import os
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict

from engine.knowledge.qmd_parser import iter_qmd_files, parse_qmd

BOOKS = [
    {"slug": "osca", "title": "Orchestrating Single-Cell Analysis with Bioconductor"},
    {"slug": "osca-basic", "title": "Orchestrating Single-Cell Analysis: Basics"},
    {"slug": "osca-advanced", "title": "Orchestrating Single-Cell Analysis: Advanced"},
    {"slug": "osta", "title": "Orchestrating Spatial Transcriptomics Analysis with Bioconductor"},
    {"slug": "oma", "title": "Orchestrating Microbiome Analysis with Bioconductor"},
    {"slug": "rnaseq-gene", "title": "RNA-Seq Workflow: Gene-Level Exploratory Analysis & Differential Expression"},
    {"slug": "tidyomics", "title": "Tidyomics Analysis Tutorials & Workflows"},
    {"slug": "tidy-spatial", "title": "Workshop Materials for Tidy Spatial Analysis"},
    {"slug": "mofa2", "title": "Multi-Omics Factor Analysis (MOFA2) Workflows"},
    {"slug": "scrapbook", "title": "Single-cell RNA-seq analyses with scrapper"},
    {"slug": "r-for-mass-spectrometry", "title": "R for Mass Spectrometry"},
    {"slug": "metabonaut", "title": "Metabonaut (Metabolomics)"},
]

def index_package_docs(
    json_path: str | Path,
    db_path: str | Path,
) -> Dict[str, int]:
    """Ingest extracted R package documentation and vignettes into bioc_knowledge."""
    json_file = Path(json_path).resolve()
    db_file = Path(db_path).resolve()

    if not json_file.exists():
        raise FileNotFoundError(f"Package docs JSON not found: {json_file}")
    if not db_file.exists():
        raise FileNotFoundError(f"Database not found at {db_file}")

    with open(json_file, "r", encoding="utf-8") as f:
        packages_data = json.load(f)

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    stats = {}
    total_added = 0

    for pkg_data in packages_data:
        pkg_name = pkg_data["package"]
        version = pkg_data.get("version", "")
        pkg_title = pkg_data.get("title", pkg_name)
        url = pkg_data.get("url", "")
        license_str = pkg_data.get("license", "")
        slug = f"pkg:{pkg_name.lower()}"
        full_title = f"Package: {pkg_name} ({version})"

        # Delete existing entries for this package slug to allow idempotent re-indexing
        cur.execute("DELETE FROM bioc_knowledge WHERE book_slug = ?", (slug,))

        # Update knowledge_sources table if present
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_sources'").fetchone():
            meta = {
                "repository": url,
                "commit": version,
                "attribution": f"The authors and maintainers of R package {pkg_name}",
                "license_statements": [{"path": "DESCRIPTION", "statement": license_str, "url": url}],
            }
            cur.execute(
                "INSERT OR REPLACE INTO knowledge_sources (book_slug, metadata_json) VALUES (?, ?)",
                (slug, json.dumps(meta)),
            )

        chunk_count = 0

        # 1. Index function topics
        for topic in pkg_data.get("topics", []):
            name = topic.get("name", "")
            title = topic.get("title", "")
            aliases = topic.get("aliases", [])
            prose = topic.get("prose", "").strip()
            code = topic.get("code", "").strip()
            rd_file = topic.get("rd_file", f"{name}.Rd")

            aliases_str = ", ".join(aliases) if aliases else name
            header = f"{name}: {title}\nAliases: {aliases_str}\nPackage: {pkg_name} ({version})"

            content_parts = [header, prose]
            if code:
                content_parts.append(f"```r\n{code}\n```")
            content = "\n\n".join(p for p in content_parts if p).strip()

            heading_path = f"{pkg_name} > {name}"
            chunk_type = "function_reference" if not code else "function_doc"

            cur.execute(
                """
                INSERT INTO bioc_knowledge (
                    book_slug, book_title, chapter_title, heading_path,
                    chunk_type, prose, code, content, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    full_title,
                    f"{name}: {title}",
                    heading_path,
                    chunk_type,
                    prose,
                    code,
                    content,
                    f"man/{rd_file}",
                ),
            )
            chunk_count += 1

        # 2. Index vignettes
        for v in pkg_data.get("vignettes", []):
            v_path = Path(v["path"])
            if v_path.exists():
                try:
                    text = v_path.read_text(encoding="utf-8", errors="ignore")
                    doc = parse_qmd(text, relative_path=f"doc/{v['filename']}")
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
                                full_title,
                                f"Vignette: {doc.title}",
                                f"{pkg_name} > {heading_str}",
                                f"vignette_{block.chunk_type}",
                                block.prose.strip(),
                                block.code.strip(),
                                content,
                                doc.relative_path,
                            ),
                        )
                        chunk_count += 1
                except Exception as exc:
                    print(f"  Warning: failed to parse vignette {v_path}: {exc}")

        stats[pkg_name] = chunk_count
        total_added += chunk_count
        print(f"Indexed {chunk_count} documentation chunks for {pkg_name} (v{version}).")

    conn.commit()
    conn.close()
    print(f"Successfully indexed {total_added} package documentation chunks into {db_file}.")
    return stats

def build_index(repos_dir: str | Path, db_path: str | Path, *, strict: bool = False, books: list[dict] = None) -> Dict[str, int]:
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
    book_list = books if books is not None else BOOKS

    for book in book_list:
        slug = book["slug"]
        title = book["title"]
        book_repo = repos_path / slug / "stable"
        if not book_repo.exists():
            book_repo = repos_path / slug
        if not book_repo.exists():
            if strict:
                conn.close()
                raise FileNotFoundError(f"Missing knowledge source: {slug}")
            print(f"Warning: Repository for {slug} not found at {book_repo}")
            continue

        book_repo = book_repo.resolve()
        qmd_files = list(iter_qmd_files(book_repo))
        print(f"Indexing {slug} ({len(qmd_files)} files)...")

        book_chunk_count = 0
        for f in qmd_files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                doc = parse_qmd(text, relative_path=str(f.relative_to(book_repo)))
            except Exception as e:
                if strict:
                    conn.close()
                    raise ValueError(f"Cannot parse knowledge source: {f}") from e
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
    if len(sys.argv) < 2:
        raise SystemExit("Use scripts/setup_knowledge.py to download pinned sources, or pass an explicit source directory here.")
    default_db = Path(__file__).resolve().parent / "knowledge.db"
    build_index(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else default_db)
