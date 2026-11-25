"""The files a cell saved in its note's folder (write.csv, ggsave, saveRDS, pdf() ...), so
the note can offer them for download under the cell.

The folder is listed before and after the cell, and a file is the cell's when it is new or
its size or modification time changed. No file is read or hashed. Left out: hidden files
and folders (the R session's own files are in .omicsbase/), and the engine's top-level
runs/ (each cell's console, plots and tables, reported on their own), _site/ and _freeze/
(Quarto's website and cache). Symbolic links are not followed.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

SKIPPED_TOP_LEVEL = frozenset({"runs", "_site", "_freeze"})
# Entries looked at per listing, so a note with a very large folder stays fast.
MAX_ENTRIES = int(os.environ.get("NOTE_FILES_MAX_ENTRIES", "5000"))
# Files reported per cell.
MAX_FILES = 50
# Modification times may be rounded to seconds (some shared volumes).
MTIME_SLACK_NS = 2_000_000_000


class Snapshot:
    def __init__(self, files: dict[str, tuple[int, int]], complete: bool, started_ns: int):
        self.files = files  # path relative to the note folder -> (bytes, mtime_ns)
        self.complete = complete  # False when the listing stopped at MAX_ENTRIES
        self.started_ns = started_ns


def snapshot(note_dir: str, max_entries: int = MAX_ENTRIES) -> Snapshot:
    """The note's files with their sizes and modification times."""
    started_ns = time.time_ns()
    files: dict[str, tuple[int, int]] = {}
    seen = 0
    pending = [""]
    while pending:
        rel_dir = pending.pop()
        try:
            with os.scandir(os.path.join(note_dir, rel_dir)) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith(".") or (not rel_dir and name in SKIPPED_TOP_LEVEL):
                        continue
                    seen += 1
                    if seen > max_entries:
                        return Snapshot(files, False, started_ns)
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(rel)
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            files[rel] = (stat.st_size, stat.st_mtime_ns)
                    except OSError:
                        continue
        except OSError:
            continue
    return Snapshot(files, True, started_ns)


def cell_files(note_dir: str, before: Snapshot, thread_id: str, base_url: str,
               max_files: int = MAX_FILES) -> dict[str, Any]:
    """{"files": [{"path", "bytes", "url"}, ...]} for the files created or changed since
    `before`, sorted by path, with "files_truncated": True when there were more than
    max_files. When `before` stopped early, a file it did not reach counts if it was
    modified after the cell started."""
    after = snapshot(note_dir)
    since_ns = before.started_ns - MTIME_SLACK_NS
    changed = []
    for rel, (size, mtime_ns) in after.files.items():
        old = before.files.get(rel)
        if old is not None:
            if old != (size, mtime_ns):
                changed.append((rel, size))
        elif before.complete or mtime_ns >= since_ns:
            changed.append((rel, size))
    changed.sort()
    result: dict[str, Any] = {
        "files": [
            {"path": rel, "bytes": size, "url": f"{base_url}/projects/{thread_id}/{quote(rel)}"}
            for rel, size in changed[:max_files]
        ]
    }
    if len(changed) > max_files:
        result["files_truncated"] = True
    return result
