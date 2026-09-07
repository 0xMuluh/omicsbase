"""Fuzzy search and replace engine for code edits — ported from Aider's editblock cascade algorithms."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Tuple


def fuzzy_replace(whole: str, search: str, replace: str) -> Tuple[bool, str, str | None]:
    """Apply a search-and-replace block to `whole` text using a multi-level fuzzy cascade.

    Returns:
        (success: bool, updated_text: str, diagnostic_message: str | None)
    """
    if not search and not replace:
        return True, whole, None

    # Normalise newlines for consistency
    whole_norm, whole_lines = _prep(whole)
    search_norm, search_lines = _prep(search)
    replace_norm, replace_lines = _prep(replace)

    # 1. New file creation / append to empty
    if not whole.strip() and not search.strip():
        return True, replace, None

    # 2. Append mode if search is empty
    if not search.strip():
        new_text = whole + ("\n" if whole and not whole.endswith("\n") else "") + replace
        return True, new_text, None

    # 3. Exact substring match (for single line or inline snippets)
    if search in whole:
        return True, whole.replace(search, replace, 1), None

    # 4. Exact line block match
    res = _perfect_replace(whole_lines, search_lines, replace_lines)
    if res is not None:
        return True, res, None

    # 4. Leading whitespace / indentation flexibility
    res = _replace_with_flexible_whitespace(whole_lines, search_lines, replace_lines)
    if res is not None:
        return True, res, None

    # 5. Drop spurious leading blank line in SEARCH (LLM artifact)
    if len(search_lines) > 2 and not search_lines[0].strip():
        skip_blank_search = search_lines[1:]
        res = _perfect_replace(whole_lines, skip_blank_search, replace_lines)
        if res is not None:
            return True, res, None
        res = _replace_with_flexible_whitespace(whole_lines, skip_blank_search, replace_lines)
        if res is not None:
            return True, res, None

    # 6. Try '...' elision markers
    try:
        res = _try_dotdotdots(whole_norm, search_norm, replace_norm)
        if res is not None:
            return True, res, None
    except ValueError:
        pass

    # If all cascades failed, generate a rich diagnostic with "Did you mean?" lines
    diagnostic = _build_failure_diagnostic(search, whole)
    return False, whole, diagnostic


def _prep(content: str) -> Tuple[str, list[str]]:
    if content and not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines(keepends=True)
    return content, lines


def _perfect_replace(whole_lines: list[str], part_lines: list[str], replace_lines: list[str]) -> str | None:
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    if part_len == 0 or part_len > len(whole_lines):
        return None

    for i in range(len(whole_lines) - part_len + 1):
        if tuple(whole_lines[i : i + part_len]) == part_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + part_len :]
            return "".join(res)
    return None


def _replace_with_flexible_whitespace(
    whole_lines: list[str], part_lines: list[str], replace_lines: list[str]
) -> str | None:
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]

    if leading and min(leading) > 0:
        num_leading = min(leading)
        part_lines = [p[num_leading:] if p.strip() else p for p in part_lines]
        replace_lines = [p[num_leading:] if p.strip() else p for p in replace_lines]

    num_part_lines = len(part_lines)
    if num_part_lines == 0 or num_part_lines > len(whole_lines):
        return None

    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = _match_but_for_leading_whitespace(
            whole_lines[i : i + num_part_lines], part_lines
        )
        if add_leading is not None:
            adjusted_replace = [add_leading + rline if rline.strip() else rline for rline in replace_lines]
            res = whole_lines[:i] + adjusted_replace + whole_lines[i + num_part_lines :]
            return "".join(res)
    return None


def _match_but_for_leading_whitespace(whole_chunk: list[str], part_lines: list[str]) -> str | None:
    num = len(whole_chunk)
    if not all(whole_chunk[i].lstrip() == part_lines[i].lstrip() for i in range(num)):
        return None

    add_set = set(
        whole_chunk[i][: len(whole_chunk[i]) - len(part_lines[i])]
        for i in range(num)
        if whole_chunk[i].strip()
    )

    if len(add_set) != 1:
        return None

    return add_set.pop()


def _try_dotdotdots(whole: str, part: str, replace: str) -> str | None:
    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE)

    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)

    if len(part_pieces) == 1:
        return None

    # Case A: Both search and replace use matching ... markers
    if len(part_pieces) == len(replace_pieces):
        all_dots_match = all(part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2))
        if not all_dots_match:
            raise ValueError("Unmatched ... in SEARCH/REPLACE block")

        part_chunks = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
        replace_chunks = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

        updated = whole
        for p, r in zip(part_chunks, replace_chunks):
            if not p and not r:
                continue
            if not p and r:
                if not updated.endswith("\n"):
                    updated += "\n"
                updated += r
                continue
            if updated.count(p) != 1:
                raise ValueError("Non-unique elision match")
            updated = updated.replace(p, r, 1)

        return updated

    # Case B: Search has ... marker but Replace does not (replacing middle range)
    if len(part_pieces) == 3 and len(replace_pieces) == 1:
        head = part_pieces[0]
        tail = part_pieces[2]
        if head and head in whole and tail and tail in whole:
            head_pos = whole.find(head)
            tail_pos = whole.find(tail, head_pos + len(head))
            if head_pos != -1 and tail_pos != -1:
                return whole[:head_pos] + replace + whole[tail_pos + len(tail):]

    return None


def _replace_closest_edit_distance(
    whole_lines: list[str], part_norm: str, part_lines: list[str], replace_lines: list[str]
) -> str | None:
    similarity_thresh = 0.90
    max_similarity = 0.0
    best_start = -1
    best_end = -1

    part_len = len(part_lines)
    if part_len == 0:
        return None

    min_len = max(1, int(part_len * 0.9))
    max_len = int(part_len * 1.1) + 1

    for length in range(min_len, max_len):
        for i in range(len(whole_lines) - length + 1):
            chunk = "".join(whole_lines[i : i + length])
            similarity = SequenceMatcher(None, chunk, part_norm).ratio()
            if similarity > max_similarity and similarity >= similarity_thresh:
                max_similarity = similarity
                best_start = i
                best_end = i + length

    if max_similarity < similarity_thresh or best_start == -1:
        return None

    modified = whole_lines[:best_start] + replace_lines + whole_lines[best_end:]
    return "".join(modified)


def find_similar_lines(search_lines_str: str, content_str: str, threshold: float = 0.55) -> str:
    """Find and return the most similar block of lines from `content_str` to suggest in errors."""
    search_lines = search_lines_str.splitlines()
    content_lines = content_str.splitlines()
    if not search_lines or not content_lines:
        return ""

    best_ratio = 0.0
    best_match: list[str] = []
    best_i = 0

    for i in range(max(1, len(content_lines) - len(search_lines) + 1)):
        chunk = content_lines[i : i + len(search_lines)]
        ratio = SequenceMatcher(None, search_lines, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = chunk
            best_i = i

    if best_ratio < threshold or not best_match:
        return ""

    context_start = max(0, best_i - 2)
    context_end = min(len(content_lines), best_i + len(search_lines) + 2)
    return "\n".join(content_lines[context_start:context_end])


def _build_failure_diagnostic(search: str, content: str) -> str:
    diag = "SEARCH block failed to match existing content."
    similar = find_similar_lines(search, content)
    if similar:
        diag += f"\nDid you mean to match some of these actual lines?\n```\n{similar}\n```"
    return diag
