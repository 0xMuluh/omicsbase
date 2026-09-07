"""Bounded, non-evaluating QMD/Rmd parser for Bioconductor book indexing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

MAX_QMD_BYTES = 4_000_000
MAX_QMD_FILES = 2_000
MAX_CHUNK_CHARS = 9_000
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


def _sha256(value: bytes | str) -> str:
    digest = hashlib.sha256()
    digest.update(value if isinstance(value, bytes) else value.encode("utf-8"))
    return digest.hexdigest()


def parse_qmd(text: str, relative_path: str = "document.qmd") -> QmdDocument:
    """Parse QMD without rendering or evaluating executable code."""
    frontmatter, body, frontmatter_lines = _split_frontmatter(text)
    lines = body.splitlines()
    heading_stack: list[tuple[int, str]] = []
    blocks: list[QmdBlock] = []
    prose_lines: list[str] = []
    code_lines: list[str] = []
    code_language: str | None = None
    code_start: int | None = None
    in_code = False

    def current_heading_path() -> list[str]:
        return [title for _level, title in heading_stack]

    def flush_prose() -> None:
        nonlocal prose_lines
        value = "\n".join(prose_lines).strip()
        if value:
            blocks.append(QmdBlock(
                ordinal=len(blocks),
                heading_path=current_heading_path(),
                prose=value,
                source_start_line=max(1, ordinal - len(prose_lines) + frontmatter_lines),
                source_end_line=max(1, ordinal + frontmatter_lines),
            ))
        prose_lines = []

    def flush_code(end_line: int) -> None:
        nonlocal code_lines, code_language, code_start
        value = "\n".join(code_lines).strip()
        if value:
            blocks.append(QmdBlock(
                ordinal=len(blocks),
                heading_path=current_heading_path(),
                code=value,
                code_language=code_language or "r",
                source_start_line=code_start,
                source_end_line=end_line + frontmatter_lines,
            ))
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
    try:
        parsed = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError:
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
    result: list[QmdBlock] = []
    pending: QmdBlock | None = None
    for block in blocks:
        if pending is None:
            pending = block
            continue
        if pending.heading_path == block.heading_path and pending.chunk_type == "prose" and block.chunk_type == "code":
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
    """Yield safe, bounded QMD/Rmd source files from a book repository."""
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
