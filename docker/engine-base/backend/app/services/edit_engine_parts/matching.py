"""Conservative text matching ladder for edit operations."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from app.services.apply_edits import is_path_locked
from app.services.fuzzy_replace import find_similar_lines
from app.services.edit_engine import DEFAULT_TEXT_EXTENSIONS

def _safe_replace(whole: bytes, search: str, replace: str, *, allow_multiple: bool) -> tuple[bytes | None, str, str | None]:
    try:
        text = whole.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "none", f"Target is not valid UTF-8: {exc}"
    if not search:
        updated = text + (("\n" if text and not text.endswith("\n") else "") + replace)
        return updated.encode("utf-8"), "append", None
    count = text.count(search)
    if count > 1 and not allow_multiple:
        return None, "ambiguous", f"SEARCH occurs {count} times; provide a larger unique block or allow_multiple."
    if count > 1 and allow_multiple:
        return text.replace(search, replace).encode("utf-8"), "exact_multiple", None
    if count == 1:
        return text.replace(search, replace, 1).encode("utf-8"), "exact", None

    normalised = _replace_normalised_lines(text, search, replace)
    if normalised is not None:
        return normalised.encode("utf-8"), "unicode_normalized", None
    indented = _replace_indent_flexible(text, search, replace)
    if indented is not None:
        return indented.encode("utf-8"), "indent_flexible", None
    elided = _replace_elision(text, search, replace)
    if elided is not None:
        return elided.encode("utf-8"), "elision_anchor", None
    try:
        from app.services.fuzzy_replace import _replace_closest_edit_distance
        fuzzy = _replace_closest_edit_distance(
            text.splitlines(keepends=True),
            search,
            search.splitlines(),
            replace.splitlines(keepends=True),
        )
        if fuzzy is not None:
            return fuzzy.encode("utf-8"), "fuzzy_closest", None
    except Exception:
        pass
    return None, "none", "SEARCH block failed to match exactly one location."


def safe_replace_text(whole: str, search: str, replace: str, *, allow_multiple: bool = False) -> tuple[bool, str, str, str | None]:
    """Apply the engine matcher without touching a project on disk.

    Adapters that need to validate a generated response before a filesystem
    transaction use this helper instead of the legacy first-match utility.
    The returned strategy and diagnostic are suitable for reflection prompts.
    """
    updated, strategy, diagnostic = _safe_replace(
        whole.encode("utf-8"), search, replace, allow_multiple=allow_multiple
    )
    if updated is None:
        return False, whole, strategy, diagnostic
    return True, updated.decode("utf-8"), strategy, None


def _replace_elision(whole: str, search: str, replace: str) -> str | None:
    """Apply a single explicit ``...`` line as a conservative gap anchor."""
    source_lines = whole.splitlines(keepends=True)
    wanted = search.splitlines()
    markers = [index for index, line in enumerate(wanted) if line.strip() in {"...", "…"}]
    if len(markers) != 1:
        return None
    marker = markers[0]
    prefix = wanted[:marker]
    suffix = wanted[marker + 1:]
    if not prefix or not suffix:
        return None
    def matches(start: int, expected: list[str]) -> bool:
        if start < 0 or start + len(expected) > len(source_lines):
            return False
        return [_canonical_line(line.rstrip("\r\n")) for line in source_lines[start:start + len(expected)]] == [_canonical_line(line) for line in expected]
    candidates: list[tuple[int, int]] = []
    for start in range(len(source_lines) - len(prefix) + 1):
        if not matches(start, prefix):
            continue
        suffix_start = start + len(prefix)
        for end in range(suffix_start + 1, len(source_lines) - len(suffix) + 1):
            if matches(end, suffix):
                candidates.append((start, end + len(suffix)))
    if len(candidates) != 1:
        return None
    start, end = candidates[0]
    replacement = replace.splitlines(keepends=True)
    return "".join(source_lines[:start] + replacement + source_lines[end:])


def _cross_file_candidates(base: Path, search: str, excluded: str) -> list[str]:
    """Report exact/canonical matches in sibling source files for reflection."""
    if not search.strip():
        return []
    candidates: list[str] = []
    for path in sorted(base.rglob("*")):
        if len(candidates) >= 5 or not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        if relative == excluded or relative.startswith(".omicsbase/"):
            continue
        if path.suffix.lower() not in DEFAULT_TEXT_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if search in text or _replace_normalised_lines(text, search, "") is not None:
            candidates.append(relative)
    return candidates


def _replace_indent_flexible(whole: str, search: str, replace: str) -> str | None:
    whole_lines = whole.splitlines(keepends=True)
    search_lines = search.splitlines()
    if not search_lines or len(search_lines) > len(whole_lines):
        return None
    candidates: list[int] = []
    for index in range(len(whole_lines) - len(search_lines) + 1):
        actual = [line.rstrip("\r\n") for line in whole_lines[index : index + len(search_lines)]]
        wanted = [line.rstrip("\r\n") for line in search_lines]
        if not all(_canonical_line(left).lstrip() == _canonical_line(right).lstrip() for left, right in zip(actual, wanted)):
            continue
        deltas = [len(left) - len(left.lstrip()) - (len(right) - len(right.lstrip())) for left, right in zip(actual, wanted) if right.strip()]
        if deltas and len(set(deltas)) == 1:
            candidates.append(index)
    if len(candidates) != 1:
        return None
    index = candidates[0]
    delta = next((len(whole_lines[index + offset]) - len(whole_lines[index + offset].lstrip()) - (len(search_lines[offset]) - len(search_lines[offset].lstrip())) for offset in range(len(search_lines)) if search_lines[offset].strip()), 0)
    replacement_lines: list[str] = []
    for line in replace.splitlines(keepends=True):
        if line.strip():
            leading = len(line) - len(line.lstrip())
            replacement_lines.append(" " * max(0, leading + delta) + line.lstrip())
        else:
            replacement_lines.append(line)
    return "".join(whole_lines[:index] + replacement_lines + whole_lines[index + len(search_lines) :])


def _replace_normalised_lines(whole: str, search: str, replace: str) -> str | None:
    whole_lines = whole.splitlines(keepends=True)
    search_lines = search.splitlines()
    if not search_lines:
        return None
    wanted = [_canonical_line(item) for item in search_lines]
    candidates: list[int] = []
    for index in range(len(whole_lines) - len(wanted) + 1):
        actual = [_canonical_line(item) for item in whole_lines[index : index + len(wanted)]]
        if actual == wanted:
            candidates.append(index)
    if len(candidates) != 1:
        return None
    index = candidates[0]
    replacement_lines = replace.splitlines(keepends=True)
    if replace and replacement_lines and not replacement_lines[-1].endswith("\n") and whole_lines[index : index + len(wanted)][-1].endswith("\n"):
        replacement_lines[-1] += "\n"
    return "".join(whole_lines[:index] + replacement_lines + whole_lines[index + len(wanted) :])


def _canonical_line(value: str) -> str:
    translated = value.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u2212": "-",
                "\u00a0": " ",
            }
        )
    )
    return unicodedata.normalize("NFKC", translated).rstrip()


def _similar_hint(search: str, before: bytes) -> str:
    try:
        return find_similar_lines(search, before.decode("utf-8"))
    except UnicodeDecodeError:
        return ""


def _normalise_relative_path(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).replace("\\", "/").strip()
    if not raw or raw.startswith("/"):
        return None
    path = Path(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return "/".join(path.parts)


