"""QMD-first Bioconductor book knowledge ingestion and retrieval.

This module deliberately keeps scientific books separate from project artifacts.
QMD source is the canonical input; rendered HTML is never required for indexing.
The database stores immutable book snapshots so an analysis can cite the exact
book revision, branch, and execution environment that informed it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.knowledge import (
    BiocBookDocument,
    BiocBookSnapshot,
    BiocBookSource,
    BiocKnowledgeChunk,
    BiocKnowledgeSyncRun,
    BiocKnowledgeTermDf,
)

logger = logging.getLogger(__name__)

MAX_QMD_BYTES = 4_000_000
MAX_QMD_FILES = 2_000
MAX_CHUNK_CHARS = 9_000
MAX_SEARCH_CHARS = 4_000
INDEX_VERSION = 1
DEFAULT_CHANNEL = "stable"
SUPPORTED_CHANNELS = {"stable", "preview"}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "how", "in", "is", "it", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "what", "which", "with",
}
EXCLUDED_PARTS = {
    ".git", ".github", "_book", "_site", "_freeze", "output", "node_modules",
    "renv", "data", "data-raw", "figures", "figure-html",
}


@dataclass
class QmdBlock:
    """One semantically grouped QMD section or code example."""

    ordinal: int
    heading_path: list[str] = field(default_factory=list)
    prose: str = ""
    code: str = ""
    code_language: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_type(self) -> str:
        if self.code and self.prose:
            return "mixed"
        if self.code:
            return "code"
        return "prose"

    @property
    def content(self) -> str:
        parts: list[str] = []
        if self.prose.strip():
            parts.append(self.prose.strip())
        if self.code.strip():
            language = self.code_language or "r"
            parts.append(f"```{language}\n{self.code.strip()}\n```")
        return "\n\n".join(parts).strip()


@dataclass
class QmdDocument:
    """Parsed QMD source with front matter and semantic blocks."""

    relative_path: str
    title: str
    frontmatter: dict[str, Any]
    blocks: list[QmdBlock]
    content_sha256: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: bytes | str) -> str:
    digest = hashlib.sha256()
    digest.update(value if isinstance(value, bytes) else value.encode("utf-8"))
    return digest.hexdigest()


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,}", value.lower())
        if token not in STOP_WORDS
    ]


def parse_qmd(text: str, relative_path: str = "document.qmd") -> QmdDocument:
    """Parse QMD without rendering it.

    The parser retains prose, fenced code, heading ancestry, line locations,
    and YAML front matter. It intentionally does not evaluate executable code.
    """

    frontmatter, body, frontmatter_lines = _split_frontmatter(text)
    lines = body.splitlines()
    heading_stack: list[tuple[int, str]] = []
    blocks: list[QmdBlock] = []
    prose_lines: list[str] = []
    code_lines: list[str] = []
    code_language: str | None = None
    code_start: int | None = None
    in_code = False
    ordinal = 0

    def current_heading_path() -> list[str]:
        return [title for _level, title in heading_stack]

    def flush_prose() -> None:
        nonlocal prose_lines
        value = "\n".join(prose_lines).strip()
        if value:
            blocks.append(
                QmdBlock(
                    ordinal=len(blocks),
                    heading_path=current_heading_path(),
                    prose=value,
                    source_start_line=max(1, ordinal - len(prose_lines) + frontmatter_lines),
                    source_end_line=max(1, ordinal + frontmatter_lines),
                )
            )
        prose_lines = []

    def flush_code(end_line: int) -> None:
        nonlocal code_lines, code_language, code_start
        value = "\n".join(code_lines).strip()
        if value:
            blocks.append(
                QmdBlock(
                    ordinal=len(blocks),
                    heading_path=current_heading_path(),
                    code=value,
                    code_language=code_language or "r",
                    source_start_line=code_start,
                    source_end_line=end_line + frontmatter_lines,
                )
            )
        code_lines = []
        code_language = None
        code_start = None

    for ordinal, line in enumerate(lines, start=1):
        fence = re.match(r"^\s*(```+|~~~+)\s*(.*)$", line)
        if fence:
            if not in_code:
                flush_prose()
                in_code = True
                code_start = ordinal + frontmatter_lines
                code_language = _normalise_code_language(fence.group(2))
            else:
                flush_code(ordinal)
                in_code = False
            continue

        if in_code:
            code_lines.append(line)
            continue

        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush_prose()
            level = len(heading.group(1))
            title = _strip_heading_attributes(heading.group(2))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            prose_lines.append(line)
        else:
            prose_lines.append(line)

    if in_code:
        flush_code(len(lines))
    else:
        flush_prose()

    blocks = _coalesce_blocks(blocks)
    title = str(
        frontmatter.get("title")
        or next((block.heading_path[-1] for block in blocks if block.heading_path), None)
        or Path(relative_path).stem.replace("_", " ").replace("-", " ").title()
    ).strip()
    return QmdDocument(
        relative_path=relative_path,
        title=title[:255],
        frontmatter=frontmatter,
        blocks=blocks,
        content_sha256=_sha256(text),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    if not text.startswith("---"):
        return {}, text, 0
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 0
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        return {}, text, 0
    raw = "\n".join(lines[1:closing])
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        logger.warning("Could not parse QMD front matter")
        parsed = {}
    return (parsed if isinstance(parsed, dict) else {}), "\n".join(lines[closing + 1:]), closing + 1


def _normalise_code_language(value: str) -> str:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    value = value.split()[0] if value else "r"
    return value.lstrip(".")[:32] or "r"


def _strip_heading_attributes(value: str) -> str:
    return re.sub(r"\s*\{[^}]+\}\s*$", "", value).strip()


def _coalesce_blocks(blocks: list[QmdBlock]) -> list[QmdBlock]:
    """Keep examples with their nearby explanation while bounding chunk size."""
    result: list[QmdBlock] = []
    pending: QmdBlock | None = None
    for block in blocks:
        if pending is None:
            pending = block
            continue
        same_section = pending.heading_path == block.heading_path
        if same_section and pending.chunk_type == "prose" and block.chunk_type == "code":
            pending.code = block.code
            pending.code_language = block.code_language
            pending.source_end_line = block.source_end_line
            continue
        result.extend(_split_block(pending))
        pending = block
    if pending is not None:
        result.extend(_split_block(pending))
    for index, block in enumerate(result):
        block.ordinal = index
    return result


def _split_block(block: QmdBlock) -> list[QmdBlock]:
    content = block.content
    if len(content) <= MAX_CHUNK_CHARS:
        return [block]
    paragraphs = re.split(r"\n\s*\n", block.prose.strip()) if block.prose else []
    chunks: list[QmdBlock] = []
    current: list[str] = []
    for paragraph in paragraphs or [block.prose]:
        if current and len("\n\n".join(current + [paragraph])) > MAX_CHUNK_CHARS:
            chunks.append(_copy_block(block, prose="\n\n".join(current), code=""))
            current = []
        current.append(paragraph)
    if current:
        chunks.append(_copy_block(block, prose="\n\n".join(current), code=""))
    if block.code:
        chunks.append(_copy_block(block, prose="", code=block.code))
    return chunks or [block]


def _copy_block(block: QmdBlock, *, prose: str, code: str) -> QmdBlock:
    return QmdBlock(
        ordinal=block.ordinal,
        heading_path=list(block.heading_path),
        prose=prose,
        code=code,
        code_language=block.code_language,
        source_start_line=block.source_start_line,
        source_end_line=block.source_end_line,
        metadata=dict(block.metadata),
    )


def iter_qmd_files(root: str | Path) -> Iterable[Path]:
    """Yield safe, bounded QMD source files from a book repository."""
    base = Path(root).resolve()
    if not base.exists() or not base.is_dir():
        return
    count = 0
    candidates = sorted(set(base.rglob("*.qmd")) | set(base.rglob("*.Qmd")) | set(base.rglob("*.Rmd")))
    for path in candidates:
        if count >= MAX_QMD_FILES:
            break
        relative = path.relative_to(base)
        if any(part in EXCLUDED_PARTS or part.startswith(".") for part in relative.parts):
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_QMD_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield path


def _tree_fingerprint(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(_sha256(path.read_bytes()).encode("ascii"))
    return digest.hexdigest()


def _safe_json(value: Any) -> Any:
    """Convert YAML values to JSON-safe metadata without losing structure."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _source_from_config(db: Session, entry: dict[str, Any]) -> BiocBookSource:
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
        key: _safe_json(value)
        for key, value in entry.items()
        if key not in {"slug", "title", "description", "book_url", "repository_url", "license", "stable_ref", "preview_ref", "enabled"}
    }
    source.updated_at = _now()
    db.flush()
    return source


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


def _git_command(command: list[str], *, timeout: int = 600) -> str:
    """Run a bounded read-only or repository-maintenance Git command."""
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()[-2000:]
        raise RuntimeError("git command failed: " + " ".join(command) + ": " + detail)
    return (completed.stdout or "").strip()


def _remote_ref_names(repository_url: str, kind: str) -> list[str]:
    output = _git_command(
        ["git", "ls-remote", f"--{kind}", "--refs", repository_url],
        timeout=120,
    )
    prefix = f"refs/{kind}/"
    names = []
    for line in output.splitlines():
        parts = line.split("	", 1)
        if len(parts) != 2 or not parts[1].startswith(prefix):
            continue
        names.append(parts[1][len(prefix):])
    return names


def _default_remote_branch(repository_url: str) -> str | None:
    output = _git_command(
        ["git", "ls-remote", "--symref", repository_url, "HEAD"],
        timeout=120,
    )
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("	HEAD"):
            return line[len("ref: refs/heads/"):].split("	", 1)[0]
    return None


def _version_key(value: str) -> tuple[int, ...] | None:
    cleaned = value.lstrip("vV")
    pieces = cleaned.split(".")
    if len(pieces) < 2 or not all(piece.isdigit() for piece in pieces[:2]):
        return None
    return tuple(int(piece) for piece in pieces[:4])


def _resolve_repository_reference(repository_url: str, requested: str, channel: str) -> str:
    """Resolve moving catalog aliases to a concrete stable or preview ref."""
    requested = str(requested or "").strip()
    if requested and requested.lower() not in {"default", "auto", "latest", "latest_release", "latest_release_branch", "latest_tag"}:
        return requested

    if requested.lower() in {"latest_tag"}:
        tags = _remote_ref_names(repository_url, "tags")
        candidates = [(key, tag) for tag in tags if (key := _version_key(tag)) is not None]
        if candidates:
            return max(candidates)[1]
        raise RuntimeError("No versioned release tags found for " + repository_url)

    branches = _remote_ref_names(repository_url, "heads")
    if channel == "preview":
        for preferred in ("devel", "sandbox", "main", "master"):
            if preferred in branches:
                return preferred
    else:
        release_candidates = []
        for branch in branches:
            normalized = branch.replace("-", "_").replace(".", "_")
            parts = normalized.split("_")
            if len(parts) == 3 and parts[0].upper() == "RELEASE" and all(part.isdigit() for part in parts[1:]):
                release_candidates.append((int(parts[1]), int(parts[2]), branch))
        if release_candidates:
            return max(release_candidates)[2]
        for preferred in ("release", "stable", "main", "master"):
            if preferred in branches:
                return preferred

    default = _default_remote_branch(repository_url)
    if default:
        return default
    raise RuntimeError("Could not resolve a " + channel + " ref for " + repository_url)


def _materialise_repository(entry: dict[str, Any], channel: str, storage_root: Path) -> tuple[Path, str, str | None, str]:
    """Fetch a stable/preview QMD source into a persistent shallow mirror."""
    local_path = entry.get("source_path") or entry.get("repository_path")
    requested = str(
        entry.get("stable_ref" if channel == "stable" else "preview_ref")
        or ("auto" if channel == "stable" else "devel")
    ).strip()
    if local_path:
        root = Path(str(local_path)).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Configured QMD source does not exist: {root}")
        paths = list(iter_qmd_files(root))
        return root, _tree_fingerprint(root, paths), None, requested

    repository_url = str(entry.get("repository_url") or "").strip()
    if not repository_url:
        raise ValueError("Catalog entry needs source_path or repository_url")
    if not repository_url.startswith(("https://github.com/", "https://git.bioconductor.org/")) or any(char.isspace() for char in repository_url):
        raise ValueError("Repository URL must use an approved Bioconductor/GitHub HTTPS host")
    resolved = _resolve_repository_reference(repository_url, requested, channel)
    slug = str(entry.get("slug") or "book").strip()
    safe_slug = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in slug)
    target = storage_root / "repositories" / safe_slug / channel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    # Mirrors created at build time belong to a different uid; git refuses to
    # operate on them unless the directory is marked safe for this process.
    _git_command(["git", "config", "--global", "--add", "safe.directory", str(target)], timeout=30)

    if (target / ".git").is_dir():
        _git_command(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", resolved],
            timeout=600,
        )
        _git_command(["git", "-C", str(target), "checkout", "--force", "FETCH_HEAD"], timeout=120)
        _git_command(["git", "-C", str(target), "clean", "-fdx"], timeout=120)
    else:
        if target.exists():
            shutil.rmtree(target)
        _git_command(
            ["git", "clone", "--depth", "1", "--branch", resolved, repository_url, str(target)],
            timeout=600,
        )

    commit_sha = _git_command(["git", "-C", str(target), "rev-parse", "HEAD"], timeout=30)
    paths = list(iter_qmd_files(target))
    return target, commit_sha or _tree_fingerprint(target, paths), commit_sha, resolved


def sync_catalog(
    db: Session,
    catalog_path: str | Path,
    *,
    storage_root: str | Path = "./knowledge",
    channels: Iterable[str] = ("stable", "preview"),
    only_slug: str | None = None,
) -> dict[str, Any]:
    """Synchronise configured QMD books into immutable database snapshots."""
    storage = Path(storage_root).expanduser().resolve()
    storage.mkdir(parents=True, exist_ok=True)
    entries = load_catalog(catalog_path)
    summary = {"status": "ok", "sources": 0, "snapshots": 0, "documents": 0, "chunks": 0, "errors": []}

    for entry in entries:
        if only_slug and str(entry.get("slug")) != only_slug:
            continue
        source = _source_from_config(db, entry)
        if not source.enabled:
            continue
        summary["sources"] += 1
        for channel in channels:
            if channel not in SUPPORTED_CHANNELS:
                raise ValueError(f"Unsupported Bioconductor knowledge channel: {channel}")
            run = BiocKnowledgeSyncRun(
                id=str(uuid.uuid4()),
                source_id=source.id,
                channel=channel,
                status="running",
                started_at=_now(),
            )
            db.add(run)
            db.flush()
            try:
                root, snapshot_key, commit_sha, resolved_ref = _materialise_repository(entry, channel, storage)
                configured_ref = str(entry.get("stable_ref" if channel == "stable" else "preview_ref") or channel)
                run.requested_ref = resolved_ref
                run.resolved_ref = commit_sha or snapshot_key
                run.run_metadata = {
                    "configured_ref": configured_ref,
                    "resolved_ref": resolved_ref,
                    "repository_url": source.repository_url,
                }
                snapshot = _index_snapshot(
                    db,
                    source=source,
                    root=root,
                    channel=channel,
                    requested_ref=run.requested_ref,
                    snapshot_key=snapshot_key,
                    commit_sha=commit_sha,
                    source_url=source.book_url,
                    repository_url=source.repository_url,
                )
                run.status = "completed"
                run.finished_at = _now()
                run.files_seen = snapshot.document_count
                run.documents_indexed = snapshot.document_count
                run.chunks_indexed = snapshot.chunk_count
                summary["snapshots"] += 1
                summary["documents"] += snapshot.document_count
                summary["chunks"] += snapshot.chunk_count
            except Exception as exc:
                error_text = str(exc)[:4000]
                # A failed source must not poison the session for the next
                # book/channel. Recreate the audit row after rolling back the
                # failed indexing transaction.
                db.rollback()
                source = _source_from_config(db, entry)
                run = BiocKnowledgeSyncRun(
                    id=str(uuid.uuid4()),
                    source_id=source.id,
                    channel=channel,
                    status="failed",
                    requested_ref=str(entry.get("stable_ref" if channel == "stable" else "preview_ref") or channel),
                    error=error_text,
                    started_at=_now(),
                    finished_at=_now(),
                )
                db.add(run)
                summary["errors"].append({"slug": source.slug, "channel": channel, "error": error_text})
                logger.exception("Bioconductor knowledge sync failed for %s/%s", source.slug, channel)
            db.commit()

    if summary["errors"]:
        summary["status"] = "partial"
    return summary


def _index_snapshot(
    db: Session,
    *,
    source: BiocBookSource,
    root: Path,
    channel: str,
    requested_ref: str,
    snapshot_key: str,
    commit_sha: str | None,
    source_url: str | None,
    repository_url: str | None,
) -> BiocBookSnapshot:
    existing = (
        db.query(BiocBookSnapshot)
        .filter(
            BiocBookSnapshot.source_id == source.id,
            BiocBookSnapshot.channel == channel,
            BiocBookSnapshot.snapshot_key == snapshot_key,
        )
        .one_or_none()
    )
    if existing is not None and existing.status in {"published", "preview"}:
        if existing.requested_ref != requested_ref:
            existing.requested_ref = requested_ref[:255]
            existing.updated_at = _now()
            db.flush()
        return existing

    if existing is None:
        snapshot = BiocBookSnapshot(
            id=str(uuid.uuid4()),
            source_id=source.id,
            channel=channel,
            requested_ref=requested_ref[:255],
            snapshot_key=snapshot_key[:128],
            commit_sha=(commit_sha or snapshot_key)[:128],
            status="staged",
            snapshot_metadata={"index_version": INDEX_VERSION},
        )
        db.add(snapshot)
        db.flush()
    else:
        snapshot = existing
        snapshot.status = "staged"
        snapshot.requested_ref = requested_ref[:255]
        snapshot.commit_sha = (commit_sha or snapshot_key)[:128]
        db.query(BiocBookDocument).filter(BiocBookDocument.snapshot_id == snapshot.id).delete()

    document_count = 0
    chunk_count = 0
    term_doc_counts: Counter[str] = Counter()
    paths = list(iter_qmd_files(root))
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        text = path.read_text(errors="replace")
        parsed = parse_qmd(text, relative_path)
        document = BiocBookDocument(
            id=str(uuid.uuid4()),
            snapshot_id=snapshot.id,
            relative_path=relative_path,
            title=parsed.title,
            content_sha256=parsed.content_sha256,
            frontmatter=_safe_json(parsed.frontmatter),
            source_url=_source_file_url(source_url, repository_url, channel, snapshot.commit_sha, relative_path),
        )
        db.add(document)
        db.flush()
        document_count += 1
        for block in parsed.blocks:
            if not block.content.strip():
                continue
            search_text = " ".join(_tokens(f"{' '.join(block.heading_path)} {block.content}"))
            for term in set(search_text.split()):
                term_doc_counts[term] += 1
            chunk = BiocKnowledgeChunk(
                id=str(uuid.uuid4()),
                document_id=document.id,
                ordinal=block.ordinal,
                chunk_type=block.chunk_type,
                heading_path=block.heading_path,
                prose=block.prose[:MAX_CHUNK_CHARS],
                code=block.code[:MAX_CHUNK_CHARS] or None,
                code_language=block.code_language,
                content=block.content[:MAX_CHUNK_CHARS],
                search_text=search_text,
                content_sha256=_sha256(block.content),
                source_start_line=block.source_start_line,
                source_end_line=block.source_end_line,
                chunk_metadata=_safe_json(block.metadata),
            )
            db.add(chunk)
            chunk_count += 1
    db.query(BiocKnowledgeTermDf).filter(BiocKnowledgeTermDf.snapshot_id == snapshot.id).delete()
    for term, doc_count in term_doc_counts.items():
        db.add(BiocKnowledgeTermDf(snapshot_id=snapshot.id, term=term[:128], doc_count=doc_count))
    snapshot.document_count = document_count
    snapshot.chunk_count = chunk_count
    snapshot.status = "published" if channel == "stable" else "preview"
    snapshot.published_at = _now()
    snapshot.updated_at = _now()
    db.flush()
    _supersede_previous_snapshots(db, snapshot)
    return snapshot


def _supersede_previous_snapshots(db: Session, snapshot: BiocBookSnapshot) -> None:
    previous = (
        db.query(BiocBookSnapshot)
        .filter(
            BiocBookSnapshot.source_id == snapshot.source_id,
            BiocBookSnapshot.channel == snapshot.channel,
            BiocBookSnapshot.id != snapshot.id,
            BiocBookSnapshot.status.in_(["published", "preview"]),
        )
        .all()
    )
    for superseded in previous:
        superseded.status = "superseded"
        superseded.updated_at = _now()
        db.query(BiocKnowledgeTermDf).filter(BiocKnowledgeTermDf.snapshot_id == superseded.id).delete(
            synchronize_session=False
        )


def _source_file_url(
    book_url: str | None,
    repository_url: str | None,
    channel: str,
    commit_sha: str | None,
    relative_path: str,
) -> str | None:
    if repository_url and commit_sha:
        base = repository_url.rstrip("/")
        return f"{base}/blob/{commit_sha}/{relative_path}"
    return book_url


_SEARCH_CACHE: dict[tuple[str, str, int, str | None], tuple[float, list[dict[str, Any]], dict[str, Any] | None]] = {}
SEARCH_CACHE_TTL_SECONDS = 300


def search_bioc_knowledge(
    db: Session,
    query: str,
    *,
    channel: str = DEFAULT_CHANNEL,
    limit: int = 6,
    source_slug: str | None = None,
) -> dict[str, Any]:
    """Return cited QMD prose/code recipes relevant to a scientific question.

    Ranking and recall are identical to the full-scan implementation; the
    speedup comes from loading only ``search_text`` for candidate scoring and
    fetching full rows just for the top results, plus a short-lived cache for
    repeated searches within the same process.
    """
    query = " ".join(str(query or "").split())
    if not query:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}
    if channel not in SUPPORTED_CHANNELS:
        return {"status": "error", "error": f"Unsupported channel: {channel}"}

    limit = max(1, min(limit, 20))
    cache_key = (query, channel, limit, source_slug)
    now = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return {
            "status": "ok",
            "query": query,
            "matches": cached[1],
            "knowledge_snapshot": cached[2],
            "cached": True,
            "retrieval_policy": "QMD source excerpts are methodological guidance; project data and executed results remain authoritative.",
        }

    query_tokens = set(_tokens(query))
    query_terms = set(query_tokens)
    snapshots_query = db.query(BiocBookSnapshot).filter(
        BiocBookSnapshot.status == ("published" if channel == "stable" else "preview"),
        BiocBookSnapshot.channel == channel,
    )
    if source_slug:
        snapshots_query = snapshots_query.join(BiocBookSource).filter(BiocBookSource.slug == source_slug)
    snapshots = snapshots_query.all()
    snapshot_ids = [snapshot.id for snapshot in snapshots]
    if not snapshot_ids:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}

    # Phase 1: per-term document frequencies from the precomputed table and
    # candidate chunk ids via the trigram-backed substring index (a superset
    # of the token-overlap candidates, so recall cannot shrink).
    if not query_terms:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}
    df_rows = (
        db.query(BiocKnowledgeTermDf.term, func.sum(BiocKnowledgeTermDf.doc_count))
        .filter(
            BiocKnowledgeTermDf.snapshot_id.in_(snapshot_ids),
            BiocKnowledgeTermDf.term.in_(query_terms),
        )
        .group_by(BiocKnowledgeTermDf.term)
        .all()
    )
    document_frequency: dict[str, int] = {term: int(total) for term, total in df_rows}
    if document_frequency:
        conditions = [
            BiocKnowledgeChunk.search_text.ilike(f"%{_escape_like(term)}%", escape="\\")
            for term in query_terms
        ]
        candidate_ids = {
            chunk_id
            for (chunk_id,) in db.query(BiocKnowledgeChunk.id)
            .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
            .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
            .filter(BiocBookSnapshot.id.in_(snapshot_ids), or_(*conditions))
            .all()
        }
    else:
        # Fallback: term-frequency table not populated (pre-backfill). Rebuild
        # the frequencies from a narrow projection with identical semantics.
        narrow_rows = (
            db.query(BiocKnowledgeChunk.id, BiocKnowledgeChunk.search_text)
            .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
            .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
            .filter(BiocBookSnapshot.id.in_(snapshot_ids))
            .all()
        )
        document_frequency = {}
        candidate_ids = set()
        for chunk_id, search_text in narrow_rows:
            terms = set((search_text or "").split())
            for term in terms:
                document_frequency[term] = document_frequency.get(term, 0) + 1
            if query_terms & terms:
                candidate_ids.add(chunk_id)
    if not candidate_ids:
        return {"status": "ok", "query": query, "matches": [], "knowledge_snapshot": None}

    # Phase 2: full rows only for candidates; scoring identical to the
    # previous full-scan pass.
    chunk_rows = (
        db.query(BiocKnowledgeChunk, BiocBookDocument, BiocBookSnapshot, BiocBookSource)
        .join(BiocBookDocument, BiocKnowledgeChunk.document_id == BiocBookDocument.id)
        .join(BiocBookSnapshot, BiocBookDocument.snapshot_id == BiocBookSnapshot.id)
        .join(BiocBookSource, BiocBookSnapshot.source_id == BiocBookSource.id)
        .filter(BiocKnowledgeChunk.id.in_(candidate_ids))
        .all()
    )
    ranked: list[tuple[float, tuple[Any, Any, Any, Any]]] = []
    for row in chunk_rows:
        chunk, document, snapshot, source = row
        terms = set((chunk.search_text or "").split())
        overlap = query_terms & terms
        if not overlap:
            continue
        score = 0.0
        for term in overlap:
            score += 1.0 + (1.0 / max(1, document_frequency.get(term, 1)))
        searchable = f"{document.title} {'/'.join(chunk.heading_path or [])} {chunk.content}".lower()
        if query.lower() in searchable:
            score += 6.0
        if chunk.code:
            score += 0.25
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1][1].relative_path, item[1][0].ordinal))
    matches = []
    for score, (chunk, document, snapshot, source) in ranked[:limit]:
        matches.append(
            {
                "score": round(score, 3),
                "book_slug": source.slug,
                "book_title": source.title,
                "channel": snapshot.channel,
                "bioconductor_ref": snapshot.requested_ref,
                "commit_sha": snapshot.commit_sha,
                "document": document.relative_path,
                "title": document.title,
                "heading_path": chunk.heading_path or [],
                "chunk_type": chunk.chunk_type,
                "prose": (chunk.prose or "")[:MAX_SEARCH_CHARS],
                "code": (chunk.code or "")[:MAX_SEARCH_CHARS],
                "code_language": chunk.code_language,
                "source_start_line": chunk.source_start_line,
                "source_end_line": chunk.source_end_line,
                "source_url": document.source_url,
                "citation": _citation(source, snapshot, document, chunk),
            }
        )
    _SEARCH_CACHE[cache_key] = (now, matches, _snapshot_label(snapshots))
    if len(_SEARCH_CACHE) > 128:
        _SEARCH_CACHE.clear()
    return {
        "status": "ok",
        "query": query,
        "matches": matches,
        "knowledge_snapshot": _snapshot_label(snapshots),
        "retrieval_policy": "QMD source excerpts are methodological guidance; project data and executed results remain authoritative.",
    }


def _citation(source: BiocBookSource, snapshot: BiocBookSnapshot, document: BiocBookDocument, chunk: BiocKnowledgeChunk) -> str:
    heading = " > ".join(chunk.heading_path or [document.title])
    return f"{source.title}, {heading} ({snapshot.requested_ref}, {snapshot.commit_sha[:12]})"


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user query terms match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snapshot_label(snapshots: list[BiocBookSnapshot]) -> dict[str, Any] | None:
    if not snapshots:
        return None
    return {
        "channel": snapshots[0].channel,
        "books": [
            {
                "source_id": str(snapshot.source_id),
                "commit_sha": snapshot.commit_sha,
                "ref": snapshot.requested_ref,
                "status": snapshot.status,
            }
            for snapshot in snapshots
        ],
    }


def knowledge_status(db: Session) -> dict[str, Any]:
    sources = db.query(BiocBookSource).order_by(BiocBookSource.title.asc()).all()
    snapshots = db.query(BiocBookSnapshot).order_by(BiocBookSnapshot.updated_at.desc()).all()
    runs = db.query(BiocKnowledgeSyncRun).order_by(BiocKnowledgeSyncRun.started_at.desc()).limit(20).all()
    return {
        "index_version": INDEX_VERSION,
        "sources": [
            {
                "id": str(source.id),
                "slug": source.slug,
                "stable_ref": source.stable_ref,
                "preview_ref": source.preview_ref,
                "title": source.title,
                "enabled": source.enabled,
                "book_url": source.book_url,
                "repository_url": source.repository_url,
                "license": source.license,
            }
            for source in sources
        ],
        "snapshots": [
            {
                "id": str(snapshot.id),
                "source_id": str(snapshot.source_id),
                "channel": snapshot.channel,
                "ref": snapshot.requested_ref,
                "commit_sha": snapshot.commit_sha,
                "status": snapshot.status,
                "document_count": snapshot.document_count,
                "chunk_count": snapshot.chunk_count,
                "updated_at": snapshot.updated_at,
            }
            for snapshot in snapshots
        ],
        "recent_syncs": [
            {
                "id": str(run.id),
                "source_id": str(run.source_id),
                "channel": run.channel,
                "status": run.status,
                "requested_ref": run.requested_ref,
                "resolved_ref": run.resolved_ref,
                "documents_indexed": run.documents_indexed,
                "chunks_indexed": run.chunks_indexed,
                "error": run.error,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in runs
        ],
    }


__all__ = [
    "QmdBlock",
    "QmdDocument",
    "parse_qmd",
    "iter_qmd_files",
    "load_catalog",
    "sync_catalog",
    "search_bioc_knowledge",
    "knowledge_status",
]
