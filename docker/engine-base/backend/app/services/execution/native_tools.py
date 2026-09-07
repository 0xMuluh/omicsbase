"""Deterministic workspace tool primitives for the OmicsBase native harness.

Provides exact-match file editing, line-numbered file viewing, recursive pattern search,
directory tree listing, full file writes, and asynchronous subprocess execution.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Callable

MAX_READ_CHARS = 100_000
MAX_OUTPUT_CHARS = 100_000


def _resolve_safe_path(project_dir: Path, rel_path: str) -> Path:
    """Resolve and validate that rel_path stays strictly within project_dir."""
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw:
        return project_dir.resolve()

    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Path must be a relative path within project: {rel_path!r}")

    resolved = (project_dir / raw).resolve()
    try:
        resolved.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes project boundary: {rel_path!r}") from exc
    return resolved


def view_file(
    project_dir: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """View file contents with line numbers and 1-indexed line range slicing."""
    try:
        target = _resolve_safe_path(project_dir, path)
        if not target.exists():
            return {"status": "error", "error": f"File not found: {path}"}
        if target.is_dir():
            return {"status": "error", "error": f"Target is a directory, not a file: {path}"}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"status": "error", "error": f"Could not read file {path}: {exc}"}

        lines = content.splitlines()
        total_lines = len(lines)

        if total_lines == 0:
            return {
                "status": "ok",
                "path": path,
                "total_lines": 0,
                "start_line": 1,
                "end_line": 0,
                "content": "(empty file)",
            }

        s = max(1, int(start_line)) if start_line is not None else 1
        e = min(total_lines, int(end_line)) if end_line is not None else total_lines

        if s > total_lines:
            return {
                "status": "error",
                "error": f"start_line ({s}) exceeds total line count ({total_lines})",
            }
        if s > e:
            return {
                "status": "error",
                "error": f"start_line ({s}) must be <= end_line ({e})",
            }

        sliced = lines[s - 1 : e]
        numbered = [f"{s + idx:5d} | {line}" for idx, line in enumerate(sliced)]
        rendered = "\n".join(numbered)

        if len(rendered) > MAX_READ_CHARS:
            rendered = rendered[:MAX_READ_CHARS] + "\n... [content truncated due to length]"

        return {
            "status": "ok",
            "path": path,
            "total_lines": total_lines,
            "start_line": s,
            "end_line": s + len(sliced) - 1,
            "content": rendered,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def replace_file_content(
    project_dir: Path,
    path: str,
    target_content: str,
    replacement_content: str,
    allow_multiple: bool = False,
) -> dict[str, Any]:
    """Replace an exact character-sequence in a file with strict uniqueness verification."""
    try:
        target = _resolve_safe_path(project_dir, path)
        if not target.exists():
            return {"status": "error", "error": f"File not found: {path}"}
        if target.is_dir():
            return {"status": "error", "error": f"Target is a directory, not a file: {path}"}

        content = target.read_text(encoding="utf-8")

        if not target_content:
            return {"status": "error", "error": "target_content cannot be empty"}

        if target_content not in content:
            return {
                "status": "error",
                "error": (
                    f"target_content not found in {path}. "
                    "Ensure indentation, line endings, and whitespace match exactly."
                ),
            }

        count = content.count(target_content)
        if count > 1 and not allow_multiple:
            return {
                "status": "error",
                "error": (
                    f"target_content occurs {count} times in {path}. "
                    "Include more surrounding context lines to make the target unique, "
                    "or set allow_multiple=True."
                ),
            }

        new_content = content.replace(target_content, replacement_content) if allow_multiple else content.replace(target_content, replacement_content, 1)
        target.write_text(new_content, encoding="utf-8")

        return {
            "status": "ok",
            "path": path,
            "replacements": count if allow_multiple else 1,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def write_to_file(
    project_dir: Path,
    path: str,
    content: str,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Create or overwrite a full file, auto-creating parent directories."""
    try:
        target = _resolve_safe_path(project_dir, path)
        if target.exists() and not overwrite:
            return {
                "status": "error",
                "error": f"File already exists and overwrite=False: {path}",
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return {
            "status": "ok",
            "path": path,
            "bytes_written": len(content.encode("utf-8")),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def list_dir(
    project_dir: Path,
    path: str | None = None,
    recursive: bool = False,
) -> dict[str, Any]:
    """List files and directories relative to project directory."""
    try:
        target = _resolve_safe_path(project_dir, path or "")
        if not target.exists():
            return {"status": "error", "error": f"Directory not found: {path or '.'}"}
        if not target.is_dir():
            return {"status": "error", "error": f"Path is not a directory: {path or '.'}"}

        entries: list[dict[str, Any]] = []
        project_root = project_dir.resolve()

        if recursive:
            for root, dirs, files in os.walk(target):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".git"}
                ]
                rel_root = Path(root).relative_to(project_root).as_posix()
                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    p = (Path(root) / f).relative_to(project_root).as_posix()
                    entries.append({"path": p, "type": "file"})
                for d in sorted(dirs):
                    p = (Path(root) / d).relative_to(project_root).as_posix()
                    entries.append({"path": p, "type": "directory"})
        else:
            for item in sorted(target.iterdir()):
                if item.name.startswith(".") or item.name in {"node_modules", "__pycache__"}:
                    continue
                rel = item.relative_to(project_root).as_posix()
                entries.append({
                    "name": item.name,
                    "path": rel,
                    "type": "directory" if item.is_dir() else "file",
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                })

        return {
            "status": "ok",
            "base_path": path or ".",
            "count": len(entries),
            "entries": entries[:500],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def grep_search(
    project_dir: Path,
    query: str,
    path: str | None = None,
    is_regex: bool = False,
    case_insensitive: bool = True,
) -> dict[str, Any]:
    """Fast regex or literal text pattern search across workspace files."""
    try:
        target = _resolve_safe_path(project_dir, path or "")
        flags = re.IGNORECASE if case_insensitive else 0

        pattern = query if is_regex else re.escape(query)
        regex = re.compile(pattern, flags)
        project_root = project_dir.resolve()
        matches: list[dict[str, Any]] = []

        files_to_check: list[Path] = []
        if target.is_file():
            files_to_check.append(target)
        else:
            for root, dirs, files in os.walk(target):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".git"}
                ]
                for f in files:
                    if not f.startswith("."):
                        files_to_check.append(Path(root) / f)

        for fpath in files_to_check:
            if len(matches) >= 100:
                break
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel_p = fpath.relative_to(project_root).as_posix()
                            matches.append({
                                "file": rel_p,
                                "line": line_num,
                                "content": line.rstrip()[:200],
                            })
                            if len(matches) >= 100:
                                break
            except Exception:
                continue

        return {
            "status": "ok",
            "query": query,
            "matches_count": len(matches),
            "matches": matches,
            "truncated": len(matches) >= 100,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def run_command(
    project_dir: Path,
    command: str,
    timeout_seconds: int = 180,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute a shell command asynchronously in the project directory."""
    try:
        abs_cwd = project_dir.resolve().as_posix()
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=abs_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        try:
            stdout_data, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=max(1, timeout_seconds),
            )
            output = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            exit_code = proc.returncode
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except Exception:
                pass
            return {
                "status": "error",
                "error": f"Command timed out after {timeout_seconds} seconds",
                "exit_code": -1,
                "output": "",
            }

        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"

        return {
            "status": "ok" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "output": output,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "exit_code": -1, "output": ""}
