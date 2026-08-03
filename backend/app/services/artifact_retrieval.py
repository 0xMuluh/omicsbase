"""Incremental, project-local retrieval over code, reports, and result artifacts."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

# Version 2 adds a cheap filesystem-stat fingerprint alongside the content
# fingerprint. Retrieval is called while building agent context, so hashing
# every candidate file on every turn becomes noticeable in larger projects.
INDEX_VERSION = 2
INDEX_PATH = Path("output/derived/.omicsbase_artifact_index.json")
MAX_FILES = 500
MAX_FILE_BYTES = 2_000_000
MAX_INDEX_CHARS = 40_000
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "how", "in", "is", "it", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "what", "which", "with",
}
SEMANTIC_GROUPS = (
    {"alpha", "shannon", "simpson", "richness", "diversity"},
    {"beta", "bray", "jaccard", "ordination", "pcoa", "community"},
    {"permanova", "adonis", "adonis2", "dispersion", "permutation"},
    {"differential", "abundance", "limrots", "ancombc", "aldex2", "deseq2"},
    {"longitudinal", "repeated", "mixed", "lmm", "lmer", "trajectory"},
    {"metabolite", "metabolomics", "limma", "feature", "panel"},
    {"failed", "failure", "error", "repair", "render"},
    {"group", "grouping", "contrast", "covariate", "design"},
)


def search_workspace(
    project_dir: str,
    query: str,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Return ranked artifact matches, incrementally refreshing the local index."""
    base = Path(project_dir).resolve()
    if not query.strip() or not base.exists():
        return {"status": "error", "error": "A generated project and non-empty query are required."}

    index, changed = _refresh_index(base)
    if changed:
        _write_index(base, index)
    documents = list((index.get("documents") or {}).values())
    query_tokens = _expand_query(_tokens(query))
    if not query_tokens:
        return {"status": "ok", "query": query, "matches": [], "indexed_files": len(documents)}

    document_frequency = Counter()
    for document in documents:
        document_frequency.update(set(document.get("terms") or {}))

    ranked = []
    for document in documents:
        score = _score_document(
            document,
            query,
            query_tokens,
            document_frequency,
            len(documents),
        )
        if score <= 0:
            continue
        ranked.append(
            {
                "path": document["path"],
                "kind": document["kind"],
                "title": document.get("title"),
                "score": round(score, 3),
                "excerpt": _matching_excerpt(document.get("text") or "", query_tokens),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["path"]))
    return {
        "status": "ok",
        "query": query,
        "matches": ranked[: max(1, min(limit, 20))],
        "indexed_files": len(documents),
        "index_refreshed": changed,
    }


def _refresh_index(base: Path) -> tuple[dict[str, Any], bool]:
    index = _load_index(base)
    documents = index.setdefault("documents", {})
    candidates = _candidate_files(base)
    candidate_paths = {path.relative_to(base).as_posix() for path in candidates}
    changed = False

    for stale_path in set(documents) - candidate_paths:
        documents.pop(stale_path, None)
        changed = True

    for path in candidates:
        relative = path.relative_to(base).as_posix()
        stat_fingerprint = _stat_fingerprint(path)
        existing = documents.get(relative) or {}
        if existing.get("stat_fingerprint") == stat_fingerprint:
            continue
        fingerprint = _fingerprint(path)
        document = _index_document(base, path, fingerprint)
        if document:
            document["stat_fingerprint"] = stat_fingerprint
            documents[relative] = document
        else:
            documents.pop(relative, None)
        changed = True
    index["version"] = INDEX_VERSION
    return index, changed


def _candidate_files(base: Path) -> list[Path]:
    candidates: list[Path] = []
    roots = (base / "code", base / "output" / "results", base / "output")
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if len(candidates) >= MAX_FILES:
                return candidates
            if path in seen or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            relative = path.relative_to(base)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.suffix.lower() not in {
                ".r", ".qmd", ".md", ".yml", ".yaml", ".json",
                ".csv", ".tsv", ".txt", ".log", ".html",
            }:
                continue
            if "output/site_libs" in relative.as_posix():
                continue
            seen.add(path)
            candidates.append(path)
    return candidates


def _index_document(base: Path, path: Path, fingerprint: str) -> dict[str, Any] | None:
    relative = path.relative_to(base).as_posix()
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        text, title = _tabular_text(path)
        kind = "result_table" if relative.startswith("output/results/") else "table"
    else:
        raw = path.read_text(errors="replace")[:MAX_INDEX_CHARS]
        text = _strip_html(raw) if suffix == ".html" else raw
        title = _title(text, path.stem)
        kind = _artifact_kind(relative, suffix)
    normalized = " ".join(text.split())[:MAX_INDEX_CHARS]
    if not normalized:
        return None
    weighted_text = f"{relative} {relative} {relative} {title} {title} {normalized}"
    return {
        "path": relative,
        "kind": kind,
        "title": title,
        "fingerprint": fingerprint,
        "terms": dict(Counter(_tokens(weighted_text))),
        "text": normalized,
    }


def _tabular_text(path: Path) -> tuple[str, str]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    rows: list[dict[str, str]] = []
    with path.open(errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = reader.fieldnames or []
        for index, row in enumerate(reader):
            if index >= 100:
                break
            rows.append(row)
    values = " ".join(
        f"{column} {value}"
        for row in rows[:20]
        for column, value in row.items()
        if value not in (None, "")
    )
    return (
        f"Columns: {', '.join(columns)}. Preview values: {values}",
        path.stem.replace("_", " ").title(),
    )


def _score_document(
    document: dict[str, Any],
    query: str,
    query_tokens: set[str],
    document_frequency: Counter,
    document_count: int,
) -> float:
    terms = document.get("terms") or {}
    score = 0.0
    for token in query_tokens:
        frequency = terms.get(token, 0)
        if not frequency:
            continue
        inverse_frequency = math.log((document_count + 1) / (document_frequency[token] + 1)) + 1
        score += (1 + math.log(frequency)) * inverse_frequency
    query_phrase = " ".join(query.lower().split())
    searchable = f"{document.get('path', '')} {document.get('title', '')} {document.get('text', '')}".lower()
    if len(query_phrase) > 3 and query_phrase in searchable:
        score += 8
    if document.get("kind") == "result_table":
        score *= 1.15
    return score


def _matching_excerpt(text: str, query_tokens: set[str], width: int = 420) -> str:
    lowered = text.lower()
    positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
    start = max(0, (min(positions) if positions else 0) - 80)
    excerpt = text[start:start + width]
    return ("…" if start else "") + excerpt + ("…" if start + width < len(text) else "")


def _expand_query(tokens: list[str]) -> set[str]:
    expanded = set(tokens)
    for group in SEMANTIC_GROUPS:
        if expanded & group:
            expanded.update(group)
    return expanded


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower())
        if token not in STOP_WORDS
    ]


def _strip_html(value: str) -> str:
    without_scripts = re.sub(r"<(script|style).*?>.*?</\1>", " ", value, flags=re.I | re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", without_scripts))


def _title(text: str, fallback: str) -> str:
    match = re.search(r"(?m)^#{1,2}\s+(.+)$", text)
    return (match.group(1).strip() if match else fallback.replace("_", " ").title())[:200]


def _artifact_kind(relative: str, suffix: str) -> str:
    if relative.startswith("code/"):
        return "report_source" if suffix in {".qmd", ".md"} else "analysis_code"
    if relative.startswith("output/results/"):
        return "result_artifact"
    if suffix == ".html":
        return "rendered_report"
    return "artifact"


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_fingerprint(path: Path) -> str:
    stat = path.stat()
    return ":".join(
        str(value)
        for value in (
            stat.st_size,
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1_000_000_000)),
            getattr(stat, "st_ino", 0),
        )
    )


def _load_index(base: Path) -> dict[str, Any]:
    path = base / INDEX_PATH
    if not path.exists():
        return {"version": INDEX_VERSION, "documents": {}}
    try:
        index = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": INDEX_VERSION, "documents": {}}
    if index.get("version") != INDEX_VERSION:
        return {"version": INDEX_VERSION, "documents": {}}
    return index


def _write_index(base: Path, index: dict[str, Any]) -> None:
    path = base / INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, separators=(",", ":"), sort_keys=True))
