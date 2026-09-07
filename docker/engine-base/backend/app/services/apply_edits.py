"""Fuzzy SEARCH/REPLACE apply with a single ApplyResult shape for editor, repair, and UI."""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class ApplyResult:
    path: str
    ok: bool
    strategy: str = "none"  # exact | whitespace | blank_line | elision | rewrite | locked | none
    before: str | None = None
    after: str | None = None
    attempted_search: str | None = None
    hint: str | None = None
    diagnostics: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Keep diffs compact for API/stream payloads.
        if self.before is not None and self.after is not None and self.ok:
            payload["diff"] = unified_diff(self.before, self.after, self.path)
        return payload


def unified_diff(before: str, after: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


from app.services.fuzzy_replace import find_similar_lines, fuzzy_replace


def apply_search_replace(
    whole: str,
    search: str,
    replace: str,
    *,
    path: str = "",
) -> ApplyResult:
    """Apply SEARCH/REPLACE with the aider-style cascade (exact → whitespace → blank → ...)."""
    ok, after, diagnostic = fuzzy_replace(whole, search, replace)
    if ok:
        strategy = _infer_strategy(whole, search, after)
        return ApplyResult(
            path=path,
            ok=True,
            strategy=strategy,
            before=whole,
            after=after,
            attempted_search=search,
        )

    hint = find_similar_lines(search, whole)
    diagnostics = [diagnostic] if diagnostic else ["SEARCH block did not match the current file."]
    if replace and replace in whole:
        diagnostics.append("REPLACE text is already present in the file.")
    return ApplyResult(
        path=path,
        ok=False,
        strategy="none",
        before=whole,
        after=None,
        attempted_search=search,
        hint=hint or None,
        diagnostics=diagnostics,
    )


def apply_rewrite(whole: str | None, content: str, *, path: str = "") -> ApplyResult:
    before = whole if whole is not None else ""
    return ApplyResult(
        path=path,
        ok=True,
        strategy="rewrite",
        before=before,
        after=content,
        reason="Full file rewrite",
    )


def format_apply_failures(results: Iterable[ApplyResult]) -> str:
    failed = [item for item in results if not item.ok]
    if not failed:
        return ""
    blocks = "block" if len(failed) == 1 else "blocks"
    lines = [f"# {len(failed)} SEARCH/REPLACE {blocks} failed to match!", ""]
    for item in failed:
        lines.append(f"## Failed in {item.path or '(unknown path)'}")
        for diagnostic in item.diagnostics:
            lines.append(f"- {diagnostic}")
        if item.hint:
            lines.append("Did you mean to match some of these actual lines?")
            lines.append("```")
            lines.append(item.hint)
            lines.append("```")
        if item.attempted_search:
            lines.append("SEARCH was:")
            lines.append("```")
            lines.append(item.attempted_search[:2000])
            lines.append("```")
        lines.append("")
    lines.append(
        "The SEARCH section must match an existing block (whitespace-tolerant matching is already tried)."
    )
    return "\n".join(lines)


def replace_most_similar_chunk(whole: str, part: str, replace: str) -> str | None:
    whole, whole_lines = _prep(whole)
    part, part_lines = _prep(part)
    replace, replace_lines = _prep(replace)

    res = _perfect_or_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    if len(part_lines) > 2 and not part_lines[0].strip():
        res = _perfect_or_whitespace(whole_lines, part_lines[1:], replace_lines)
        if res:
            return res

    try:
        res = _try_dotdotdots(whole, part, replace)
        if res:
            return res
    except ValueError:
        pass

    return None


def find_similar_lines(search_text: str, content: str, threshold: float = 0.6) -> str:
    search_lines = search_text.splitlines()
    content_lines = content.splitlines()
    if not search_lines or not content_lines:
        return ""

    best_ratio = 0.0
    best_match_i = 0
    best_match: list[str] | None = None
    window = max(1, len(search_lines))
    for i in range(max(1, len(content_lines) - window + 1)):
        chunk = content_lines[i : i + window]
        ratio = difflib.SequenceMatcher(
            None,
            "\n".join(search_lines),
            "\n".join(chunk),
        ).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = chunk
            best_match_i = i

    if best_match is None or best_ratio < threshold:
        return ""

    if (
        len(best_match) == len(search_lines)
        and best_match[0] == search_lines[0]
        and best_match[-1] == search_lines[-1]
    ):
        return "\n".join(best_match)

    pad = 5
    start = max(0, best_match_i - pad)
    end = min(len(content_lines), best_match_i + window + pad)
    return "\n".join(content_lines[start:end])


def _infer_strategy(whole: str, search: str, after: str) -> str:
    if search in whole:
        return "exact"
    # Heuristic labels for UI; exact cascade order is what matters.
    if "\n...\n" in search or search.strip().startswith("..."):
        return "elision"
    search_stripped = "\n".join(line.lstrip() for line in search.splitlines())
    whole_stripped = "\n".join(line.lstrip() for line in whole.splitlines())
    if search_stripped and search_stripped in whole_stripped:
        return "whitespace"
    if search.lstrip("\n") != search and search.lstrip("\n") in whole:
        return "blank_line"
    return "whitespace"


def _prep(content: str) -> tuple[str, list[str]]:
    if content and not content.endswith("\n"):
        content += "\n"
    return content, content.splitlines(keepends=True)


def _perfect_or_whitespace(
    whole_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> str | None:
    res = _perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res
    return _replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines)


def _perfect_replace(
    whole_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> str | None:
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    for i in range(len(whole_lines) - part_len + 1):
        if tuple(whole_lines[i : i + part_len]) == part_tup:
            return "".join(whole_lines[:i] + replace_lines + whole_lines[i + part_len :])
    return None


def _replace_part_with_missing_leading_whitespace(
    whole_lines: list[str],
    part_lines: list[str],
    replace_lines: list[str],
) -> str | None:
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]
    working_part = list(part_lines)
    working_replace = list(replace_lines)
    if leading and min(leading):
        num_leading = min(leading)
        working_part = [p[num_leading:] if p.strip() else p for p in working_part]
        working_replace = [p[num_leading:] if p.strip() else p for p in working_replace]

    num_part_lines = len(working_part)
    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = _match_but_for_leading_whitespace(
            whole_lines[i : i + num_part_lines],
            working_part,
        )
        if add_leading is None:
            continue
        adjusted = [
            add_leading + line if line.strip() else line for line in working_replace
        ]
        return "".join(whole_lines[:i] + adjusted + whole_lines[i + num_part_lines :])
    return None


def _match_but_for_leading_whitespace(whole_lines: list[str], part_lines: list[str]) -> str | None:
    if not all(whole_lines[i].lstrip() == part_lines[i].lstrip() for i in range(len(whole_lines))):
        return None
    prefixes = {
        whole_lines[i][: len(whole_lines[i]) - len(part_lines[i])]
        for i in range(len(whole_lines))
        if whole_lines[i].strip()
    }
    if len(prefixes) != 1:
        return None
    return prefixes.pop()


def _try_dotdotdots(whole: str, part: str, replace: str) -> str | None:
    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE)
    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)
    if len(part_pieces) != len(replace_pieces):
        raise ValueError("Unpaired ... in SEARCH/REPLACE block")
    if len(part_pieces) == 1:
        return None
    if not all(part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2)):
        raise ValueError("Unmatched ... in SEARCH/REPLACE block")

    part_chunks = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    replace_chunks = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]
    updated = whole
    for search_chunk, replace_chunk in zip(part_chunks, replace_chunks):
        if not search_chunk and not replace_chunk:
            continue
        if not search_chunk and replace_chunk:
            if not updated.endswith("\n"):
                updated += "\n"
            updated += replace_chunk
            continue
        if updated.count(search_chunk) != 1:
            raise ValueError
        updated = updated.replace(search_chunk, replace_chunk, 1)
    return updated


def safe_resolve_path(project_dir: str | Path | None, relative_path: str | None) -> Path | None:
    """Safely resolve relative_path within project_dir.

    Enforces strict path containment (relative_to check) to prevent directory traversal
    escapes (../, absolute paths, or symlink traversal). Returns None if unsafe or invalid.
    """
    if not project_dir or not relative_path:
        return None
    try:
        base = Path(project_dir).resolve()
        target = (base / str(relative_path).strip().lstrip("/")).resolve()
        target.relative_to(base)
        return target
    except (ValueError, TypeError, RuntimeError):
        return None


def is_path_locked(project_dir: str | Path, relative_path: str) -> bool:
    locks = load_locks(project_dir)
    normalized = relative_path.replace("\\", "/").lstrip("./")
    if normalized in locks:
        return True
    return any(
        normalized == lock or normalized.startswith(lock.rstrip("/") + "/")
        for lock in locks
    )


def load_locks(project_dir: str | Path) -> set[str]:
    path = Path(project_dir) / ".omicsbase" / "locks.json"
    if not path.exists():
        return set()
    try:
        import json

        data = json.loads(path.read_text())
        items = data.get("paths") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return set()
        return {str(item).replace("\\", "/").lstrip("./") for item in items}
    except Exception:
        return set()


def save_locks(project_dir: str | Path, paths: Iterable[str]) -> list[str]:
    import json

    base = Path(project_dir)
    lock_dir = base / ".omicsbase"
    lock_dir.mkdir(parents=True, exist_ok=True)
    normalized = sorted({str(item).replace("\\", "/").lstrip("./") for item in paths if str(item).strip()})
    (lock_dir / "locks.json").write_text(json.dumps({"paths": normalized}, indent=2) + "\n")
    return normalized
