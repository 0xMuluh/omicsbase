"""Query service for the embedded Bioconductor knowledge index."""

from __future__ import annotations

import os
import json
from urllib.parse import quote
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "knowledge.db"

def _sanitize_fts5_query(query: str) -> str:
    """Sanitize user query for SQLite FTS5 syntax."""
    terms = re.findall(r'[\w-]+', query)
    if not terms:
        return ""
    # Search for matching terms
    return " OR ".join(f'"{term}"' for term in terms)

def search_bioc_knowledge(
    query: str,
    book: Optional[str] = None,
    limit: int = 4,
    db_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Search the pinned Bioconductor QMD knowledge index.
    Returns ranked sections with prose and canonical R code snippets.
    """
    db_file = Path(db_path or DEFAULT_DB_PATH).resolve()
    if not db_file.exists():
        return {
            "status": "error",
            "error": f"Knowledge index not found at {db_file}",
            "matches": [],
            "markdown": "Bioconductor knowledge index is not available."
        }

    sanitized = _sanitize_fts5_query(query)
    if not sanitized:
        return {
            "status": "error",
            "error": "Query contains no searchable words.",
            "matches": [],
            "markdown": "No valid search terms provided."
        }

    limit = max(1, min(limit, 8))
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    try:
        if book and book.strip():
            cur.execute(
                """
                SELECT book_slug, book_title, chapter_title, heading_path,
                       chunk_type, prose, code, content, source_file, bm25(bioc_knowledge) as rank
                FROM bioc_knowledge
                WHERE bioc_knowledge MATCH ? AND book_slug = ?
                ORDER BY rank
                LIMIT ?;
                """,
                (sanitized, book.strip().lower(), limit),
            )
        else:
            cur.execute(
                """
                SELECT book_slug, book_title, chapter_title, heading_path,
                       chunk_type, prose, code, content, source_file, bm25(bioc_knowledge) as rank
                FROM bioc_knowledge
                WHERE bioc_knowledge MATCH ?
                ORDER BY rank
                LIMIT ?;
                """,
                (sanitized, limit),
            )

        rows = cur.fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {
            "status": "error",
            "error": f"FTS5 Query failed: {e}",
            "matches": [],
            "markdown": f"Search query failed: {e}"
        }

    source_metadata = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_sources'").fetchone():
        source_metadata = {slug: json.loads(data) for slug, data in conn.execute('SELECT book_slug, metadata_json FROM knowledge_sources')}
    conn.close()

    matches = []
    md_sections = []

    for row in rows:
        book_slug, book_title, chapter_title, heading_path, chunk_type, prose, code, content, source_file, rank = row
        match = {
            "book": book_slug,
            "book_title": book_title,
            "chapter": chapter_title,
            "heading": heading_path,
            "type": chunk_type,
            "prose": prose,
            "code": code,
            "source_file": source_file,
            "score": round(abs(rank), 3),
        }
        provenance = source_metadata.get(book_slug)
        if provenance:
            match['source_url'] = f"{provenance['repository']}/blob/{provenance['commit']}/{quote(source_file)}"
            match['source_commit'] = provenance['commit']
            match['attribution'] = provenance['attribution']
            match['license_statements'] = provenance['license_statements']
        matches.append(match)

        # Build clean markdown excerpt for the LLM
        sec = f"### [{book_title}] {chapter_title} - {heading_path}\n"
        if provenance:
            sec += f"*Source: [{book_slug}/{source_file}]({match['source_url']})*\n"
            sec += f"*Attribution: {provenance['attribution']}*\n"
            terms = '; '.join(item['statement'] for item in provenance['license_statements'])
            sec += f"*Source license statements: {terms}*\n\n"
        else:
            sec += f"*Source: `{book_slug}/{source_file}`*\n\n"
        if prose:
            sec += f"{prose.strip()}\n\n"
        if code:
            sec += f"```r\n{code.strip()}\n```\n"
        md_sections.append(sec)

    summary_md = f"Found {len(matches)} relevant Bioconductor references for '{query}':\n\n" + "\n---\n\n".join(md_sections) if matches else f"No relevant Bioconductor references found for '{query}'."

    return {
        "status": "success",
        "count": len(matches),
        "matches": matches,
        "markdown": summary_md,
    }

if __name__ == "__main__":
    test_query = "quality control cell filtering"
    res = search_bioc_knowledge(test_query, limit=2)
    print(f"Query: {test_query}")
    print(f"Results count: {res['count']}")
    print(res["markdown"][:600] + "...")
