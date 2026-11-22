"""Web addresses of indexed book sections, from the per-book templates in knowledge/sources.json.

A book's `web` entry is `{"template": "https://…/{page}.html", "page": <kind>, "strip_prefix": "inst/"}`:

- `path`: the source file's path without `strip_prefix` and extension (Quarto books).
- `heading`: the id of the section's level-1 heading (bookdown names each page after it).
- `stem`: the source file's name without extension (pkgdown articles, package vignettes).
"""

from __future__ import annotations

import re
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Callable, Iterable, Optional
from urllib.parse import quote

PAGE_KINDS = {"path", "heading", "stem"}


def source_url(book: dict, source_file: str) -> str:
    """The file in the book's repository at the indexed commit; empty when the book has neither."""
    repository, commit = book.get("repository"), book.get("commit")
    if not repository or not commit:
        return ""
    return f"{str(repository).rstrip('/')}/blob/{commit}/{quote(source_file)}"


def page_url(book: dict, source_file: str, page_anchor: str = "", first_heading_anchor: str = "") -> str:
    """The rendered page holding a section of `source_file`; empty when the book has no template."""
    web = book.get("web") or {}
    template, kind = web.get("template"), web.get("page")
    if not template or kind not in PAGE_KINDS:
        return ""
    path = PurePosixPath(source_file)
    if kind == "path":
        page = str(path.with_suffix(""))
        prefix = web.get("strip_prefix", "")
        if prefix and page.startswith(prefix):
            page = page[len(prefix):]
    elif kind == "stem":
        page = path.stem
    else:
        # bookdown renders index.Rmd as index.html whatever its first heading is called.
        page = "index" if path.stem == "index" else (page_anchor or first_heading_anchor)
    if not page:
        return ""
    return template.replace("{page}", quote(page))


def section_url(
    book: dict,
    source_file: str,
    anchor: str = "",
    page_anchor: str = "",
    first_heading_anchor: str = "",
) -> str:
    """The section on the book's web page, else the source file in the repository."""
    page = page_url(book, source_file, page_anchor, first_heading_anchor)
    if not page:
        return source_url(book, source_file)
    return f"{page}#{quote(anchor)}" if anchor else page


def fetch_page_ids(url: str, timeout: float = 20.0) -> Optional[set[str]]:
    """The element ids on a web page, or None when the page cannot be read."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "OmicsBase knowledge index"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read(8_000_000).decode("utf-8", errors="ignore")
    except Exception:
        return None
    return set(re.findall(r"""\bid=["']([^"']+)["']""", html))


def check_anchors(
    db_path,
    books: Iterable[dict],
    fetch: Callable[[str], Optional[set[str]]] = fetch_page_ids,
    workers: int = 4,
) -> dict[str, int]:
    """Check every stored section address against the live page, fetching each page once.

    An anchor the page has is kept; a missing anchor falls back to the chapter page; a page that
    cannot be read falls back to the source file in the repository.
    """
    by_slug = {book["slug"]: book for book in books}
    conn = sqlite3.connect(str(db_path))
    counts = {"kept": 0, "page": 0, "source": 0}
    try:
        rows = conn.execute("SELECT rowid, book_slug, source_file, anchor, url FROM sections").fetchall()
        pages = sorted({url.split("#", 1)[0] for _id, slug, source_file, _a, url in rows
                        if url and url != source_url(by_slug.get(slug, {}), source_file)})
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            ids_by_page = dict(zip(pages, pool.map(fetch, pages)))
        for rowid, slug, source_file, anchor, url in rows:
            page = url.split("#", 1)[0]
            if page not in ids_by_page:
                continue
            ids = ids_by_page[page]
            if ids is None:
                checked = source_url(by_slug.get(slug, {}), source_file)
                counts["source"] += 1
            elif "#" in url and anchor not in ids:
                checked = page
                counts["page"] += 1
            else:
                counts["kept"] += 1
                continue
            conn.execute("UPDATE sections SET url = ? WHERE rowid = ?", (checked, rowid))
        conn.execute(
            """
            UPDATE function_sections SET url = (
                SELECT s.url FROM sections s
                WHERE s.book_slug = function_sections.book_slug
                  AND s.source_file = function_sections.source_file
                  AND s.heading_path = function_sections.heading_path
            )
            WHERE EXISTS (
                SELECT 1 FROM sections s
                WHERE s.book_slug = function_sections.book_slug
                  AND s.source_file = function_sections.source_file
                  AND s.heading_path = function_sections.heading_path
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return counts
