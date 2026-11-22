"""The book badge: do the analysis functions of an R cell appear in an indexed book section?

The kernel lists the functions a cell called and where each lives (`calls.json`). `match` sorts
them into neutral, user-defined and analysis functions and looks the analysis functions up in the
`function_sections` table of the knowledge index. The badge says a function is used in that book
section; it says nothing about whether the analysis suits the data.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

LOOKUP_LOG_NAME = "knowledge-lookups.jsonl"
STATE_DIR_NAME = ".omicsbase"
# Sections listed per function; the first ones are those rule 6 prefers.
MAX_REFS_PER_FUNCTION = 10
MAX_LOOKUP_RECORDS = 2000
USER_ENVIRONMENT = "R_GlobalEnv"
PREFERRED_BOOK = "oma"

# Packages whose functions never count as analysis: base R, the tidyverse and plotting helpers,
# data packages and container infrastructure.
NEUTRAL_PACKAGES = frozenset({
    # R itself
    "base", "utils", "methods", "graphics", "grDevices", "datasets", "tools", "grid", "parallel",
    "stats4", "compiler", "tcltk", "splines",
    # tidyverse and general helpers
    "tidyverse", "dplyr", "tidyr", "tibble", "purrr", "stringr", "stringi", "forcats", "readr",
    "readxl", "magrittr", "rlang", "vctrs", "glue", "lubridate", "data.table", "reshape2", "plyr",
    "jsonlite", "here", "withr", "fs", "knitr", "rmarkdown", "kableExtra", "DT", "broom",
    # plotting helpers
    "ggplot2", "patchwork", "cowplot", "gridExtra", "scales", "RColorBrewer", "viridis",
    "viridisLite", "ggrepel", "ggbeeswarm", "ggthemes", "colorspace", "circlize",
    # data packages and data access
    "curatedMetagenomicData", "microbiomeDataSets", "HMP16SData", "MicrobiomeBenchmarkData",
    "MGnifyR", "HoloFoodR", "scRNAseq", "celldex", "STexampleData", "SFEData", "imcdatasets",
    "MOFAdata", "airway", "pasilla", "MsDataHub", "faahKO", "msdata", "ExperimentHub",
    "AnnotationHub", "TENxPBMCData", "DropletTestFiles", "BiocFileCache",
    # container infrastructure
    "SummarizedExperiment", "TreeSummarizedExperiment", "SingleCellExperiment",
    "SpatialExperiment", "MultiAssayExperiment", "S4Vectors", "IRanges", "GenomicRanges",
    "GenomeInfoDb", "Biobase", "BiocParallel", "BiocManager", "DelayedArray", "HDF5Array",
    "Matrix", "MatrixGenerics", "matrixStats", "S4Arrays", "SparseArray", "Biostrings",
    "XVector", "biomformat", "tidySummarizedExperiment", "tidySingleCellExperiment",
    "tidySpatialExperiment", "tidyseurat",
})

# Neutral except for the functions named here.
PARTLY_NEUTRAL_PACKAGES = {
    "stats": frozenset({
        "wilcox.test", "t.test", "kruskal.test", "friedman.test", "fisher.test", "chisq.test",
        "cor.test", "shapiro.test", "ks.test", "binom.test", "prop.test", "mcnemar.test",
        "mantelhaen.test", "var.test", "bartlett.test", "fligner.test", "ansari.test",
        "mood.test", "oneway.test", "quade.test", "poisson.test", "pairwise.wilcox.test",
        "pairwise.t.test", "pairwise.prop.test", "anova", "aov", "TukeyHSD", "lm", "glm", "nls",
        "loess", "manova", "p.adjust", "prcomp", "princomp", "cmdscale", "factanal", "dist",
        "hclust", "kmeans", "cutree", "cor", "mahalanobis",
    }),
    # Hosts generics whose methods live in analysis packages (DESeq2, scater, …).
    "BiocGenerics": frozenset({
        "plotPCA", "plotMA", "plotDispEsts", "estimateSizeFactors", "estimateDispersions",
        "normalize",
    }),
}

# Accessors and constructors, neutral whatever package they come from.
ACCESSORS = frozenset({
    # SummarizedExperiment family
    "assay", "assays", "assayNames", "colData", "rowData", "rowRanges", "metadata", "counts",
    "logcounts", "normcounts", "sizeFactors", "reducedDim", "reducedDims", "reducedDimNames",
    "altExp", "altExps", "altExpNames", "mainExpName", "colLabels", "rowTree", "colTree",
    "rowLinks", "colLinks", "rowTreeNames", "colTreeNames", "referenceSeq", "taxonomyRanks",
    "experiments", "sampleMap", "spatialCoords", "imgData",
    "SummarizedExperiment", "RangedSummarizedExperiment", "SingleCellExperiment",
    "TreeSummarizedExperiment", "SpatialExperiment", "MultiAssayExperiment", "ExperimentList",
    "DataFrame", "SimpleList",
    # phyloseq
    "phyloseq", "otu_table", "sample_data", "tax_table", "phy_tree", "refseq", "sample_names",
    "taxa_names", "sample_variables", "rank_names", "nsamples", "ntaxa", "taxa_are_rows",
})
ACCESSOR_PATTERN = re.compile(r"^(?:import|convert)(?:[A-Z_]|$)|^make(?:TreeSE|TreeSummarizedExperiment|Phyloseq)From")

R_KEYWORDS = frozenset({
    "if", "else", "for", "while", "repeat", "function", "return", "break", "next", "switch",
    "TRUE", "FALSE", "NULL", "NA", "Inf", "NaN", "in",
})

_LIBRARY_CALL = re.compile(
    r"""\b(?:library|require|requireNamespace|loadNamespace)\s*\(\s*["']?([A-Za-z][A-Za-z0-9.]*)["']?\s*[,)]"""
)
_FUNCTION_CALL = re.compile(
    r"(?<![\w.$@:])(?:([A-Za-z][A-Za-z0-9.]*)\s*:::?\s*)?([A-Za-z.][A-Za-z0-9._]*)\s*\("
)


def strip_r_noise(code: str, keep_strings: bool = False) -> str:
    """R code without comments (so without `#|` option lines) and backtick names; strings emptied."""
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch == "#":
            while i < n and code[i] != "\n":
                i += 1
            continue
        if ch in "\"'`":
            end = i + 1
            while end < n and code[end] != ch:
                end += 2 if code[end] == "\\" and ch != "`" else 1
            if ch != "`":
                out.append(code[i:end + 1] if keep_strings else ch + ch)
            i = end + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_library_packages(code: str) -> list[str]:
    """Packages a piece of R code attaches or loads by name, in order of appearance."""
    found = _LIBRARY_CALL.findall(strip_r_noise(code, keep_strings=True))
    return list(dict.fromkeys(found))


def extract_r_calls(code: str) -> list[tuple[Optional[str], str]]:
    """(package or None, function) for each distinct call in R code, read without parsing R."""
    calls: dict[tuple[Optional[str], str], None] = {}
    for package, name in _FUNCTION_CALL.findall(strip_r_noise(code)):
        if name in R_KEYWORDS:
            continue
        calls[(package or None, name)] = None
    return list(calls)


def classify(name: str, package: Optional[str]) -> str:
    """'user', 'neutral' or 'analysis' for a called function and the package it resolved to."""
    if package == USER_ENVIRONMENT:
        # The kernel's own helpers live in the global environment too.
        return "neutral" if name.startswith(".note_") else "user"
    if not package:
        return "neutral"  # not a function the session can see (an argument, a local helper)
    if name in ACCESSORS or ACCESSOR_PATTERN.search(name):
        return "neutral"
    if package in PARTLY_NEUTRAL_PACKAGES:
        return "analysis" if name in PARTLY_NEUTRAL_PACKAGES[package] else "neutral"
    if package in NEUTRAL_PACKAGES:
        return "neutral"
    return "analysis"


def is_map_candidate(name: str, packages: Iterable[str] = ()) -> bool:
    """Whether a function seen in book code can ever be an analysis function.

    `packages` are the packages that may own it; with none known, only the name decides.
    """
    owners = list(packages)
    if not owners:
        return classify(name, "unknown") == "analysis"
    return any(classify(name, package) == "analysis" for package in owners)


# --- look-up log -----------------------------------------------------------------------------

def lookup_log_path(note_dir: str | os.PathLike) -> Path:
    return Path(note_dir) / STATE_DIR_NAME / LOOKUP_LOG_NAME


def log_lookups(note_dir: str | os.PathLike, matches: Iterable[dict]) -> int:
    """Append the book sections a search returned to the note's look-up log."""
    records = [
        {
            "book": m.get("book"),
            "heading_path": m.get("heading"),
            "source_file": m.get("source_file"),
            "anchor": m.get("anchor", ""),
        }
        for m in matches
        if m.get("book") and not str(m["book"]).startswith("pkg:")
    ]
    if not records:
        return 0
    path = lookup_log_path(note_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(record) + "\n" for record in records))
    return len(records)


def read_lookups(note_dir: str | os.PathLike) -> list[dict]:
    """The note's most recent look-up records; empty when it has none."""
    path = lookup_log_path(note_dir)
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle.readlines()[-MAX_LOOKUP_RECORDS:]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


# --- matching --------------------------------------------------------------------------------

def _open_read_only(db: str | os.PathLike) -> Optional[sqlite3.Connection]:
    path = Path(db)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _section_rows(conn: sqlite3.Connection, call: dict, context: set[str]) -> list[tuple]:
    """Book sections using this function, judged by each chapter's package hint."""
    package = call["package"]
    rows = []
    for row in conn.execute(
        "SELECT pkg_hint, book_slug, heading_path, source_file, anchor, url "
        "FROM function_sections WHERE fn = ?",
        (call["name"],),
    ):
        hints = set(filter(None, (row[0] or "").split(",")))
        # No hint: the chapter does not say where the function comes from, so the name decides.
        # A generic hosted elsewhere (plotPCA in BiocGenerics) belongs to the chapter's package
        # when the session or the cell uses that package.
        if not hints or package in hints or (call.get("generic") and hints & context):
            rows.append(row[1:])
    return rows


def match(calls: Any, db: Any, looked_up: Optional[Iterable[dict]] = None) -> Optional[dict]:
    """Match a cell's calls against the function→sections map.

    `calls` is the kernel's calls.json (`{"calls": [{"name", "package", "generic"}], "attached": […]}`),
    `db` a knowledge index path or an open connection, `looked_up` the note's look-up records.
    Returns the `book_refs` object, or None when the cell gets no badge line: no analysis
    function, a user-defined function, or an index without the map.
    """
    listed = calls.get("calls", []) if isinstance(calls, dict) else list(calls or [])
    attached = calls.get("attached", []) if isinstance(calls, dict) else []
    analysis: list[dict] = []
    for item in listed:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        kind = classify(item["name"], item.get("package"))
        if kind == "user":
            return None
        if kind == "analysis" and not any(
            a["name"] == item["name"] and a["package"] == item["package"] for a in analysis
        ):
            analysis.append(item)
    if not analysis:
        return None

    owns_connection = not isinstance(db, sqlite3.Connection)
    conn = _open_read_only(db) if owns_connection else db
    if conn is None:
        return None
    try:
        if not _has_table(conn, "function_sections"):
            return None
        books = {}
        if _has_table(conn, "knowledge_sources"):
            for slug, data in conn.execute("SELECT book_slug, metadata_json FROM knowledge_sources"):
                try:
                    books[slug] = json.loads(data)
                except ValueError:
                    continue
        context = {p for p in list(attached) + [a["package"] for a in analysis]
                   if p and p not in NEUTRAL_PACKAGES and p not in PARTLY_NEUTRAL_PACKAGES}
        found = {index: _section_rows(conn, call, context) for index, call in enumerate(analysis)}
    finally:
        if owns_connection:
            conn.close()

    seen = {(r.get("book"), r.get("source_file"), r.get("heading_path")) for r in (looked_up or [])
            if isinstance(r, dict)}
    coverage: dict[tuple, int] = {}
    for rows in found.values():
        for section in {(slug, source_file, heading) for slug, heading, source_file, _a, _u in rows}:
            coverage[section] = coverage.get(section, 0) + 1

    def preference(section: tuple) -> tuple:
        # Rule 6: looked up in this note, then OMA, then other books; then the widest cover.
        return (section not in seen, section[0] != PREFERRED_BOOK, -coverage[section], section)

    refs: list[dict] = []
    missing: list[dict] = []
    for index, call in enumerate(analysis):
        rows = found[index]
        if not rows:
            missing.append({"function": call["name"], "package": call["package"]})
            continue
        rows.sort(key=lambda r: preference((r[0], r[2], r[1])))
        for slug, heading_path, source_file, _anchor, url in rows[:MAX_REFS_PER_FUNCTION]:
            book = books.get(slug, {})
            parts = [part.strip() for part in (heading_path or "").split(" > ") if part.strip()]
            refs.append({
                "function": call["name"],
                "package": call["package"],
                "book": slug,
                "book_short": book.get("short") or slug.upper(),
                "book_title": book.get("title") or slug,
                "chapter": parts[0] if parts else "",
                "heading": parts[-1] if parts else "",
                "url": url or "",
                "looked_up": (slug, source_file, heading_path) in seen,
                "_section": (slug, source_file, heading_path),
            })

    shown = None
    if not missing and refs:
        best = min(coverage, key=preference)
        shown = next(dict(ref) for ref in refs if ref["_section"] == best)
        shown.pop("_section")
    for ref in refs:
        ref.pop("_section")
    versions = {}
    for slug in dict.fromkeys(ref["book"] for ref in refs):
        book = books.get(slug, {})
        version = book.get("version") or str(book.get("commit") or "")[:12]
        if version:
            versions[slug] = version
    return {
        "status": "not_in_books" if missing else "from_book",
        "shown": shown,
        "refs": refs,
        "missing": missing,
        "book_versions": versions,
    }


def book_refs_for_run(run_dir: str | os.PathLike, note_dir: str | os.PathLike, db: Any) -> Optional[dict]:
    """`book_refs` for a finished run, from its calls.json; None when there is nothing to say."""
    calls_path = Path(run_dir) / "calls.json"
    if not calls_path.exists():
        return None
    with open(calls_path, "r", encoding="utf-8", errors="ignore") as handle:
        calls = json.load(handle)
    return match(calls, db, read_lookups(note_dir))
