"""One status file per cell run, on the note's volume.

The gateway writes the phases it owns (waiting for a pod), the note's own engine the rest
and the result. The file outlives both, so a run's phase, console text and result can be
read without asking the engine that runs it.
"""
import json
import os
import time
import uuid
from pathlib import Path

from engine.kernel.note_kernel import CONSOLE_FILE_NAME, KERNEL_DIR_REL, _write_atomic

STATUS_PREFIX = "exec-"
KEEP_SECONDS = 24 * 3600
# Console text returned by one status read.
OUTPUT_CHUNK_BYTES = 64 * 1024
GATEWAY_PHASES = ("waiting_for_slot", "starting_pod")
POD_PHASES = ("waiting_for_cell", "starting_r", "restoring_session", "running")
# New for every engine process: a record left in a phase by another process has no one
# working on it any more.
BOOT_ID = uuid.uuid4().hex
INTERRUPTED = {
    "success": False,
    "error": "Interrupted by a server restart. Run the cell again.",
    "markdown": "**Interrupted by a server restart.** Run the cell again.",
}


def _path(note_dir, execution_id):
    return Path(note_dir) / KERNEL_DIR_REL / f"{STATUS_PREFIX}{execution_id}.json"


def read(note_dir, execution_id):
    try:
        return json.loads(_path(note_dir, execution_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(note_dir, execution_id, record):
    path = _path(note_dir, execution_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(record))
    return record


def _enter(record, phase):
    now = time.time()
    record.update(phase=phase, phase_started_at=now, boot_id=BOOT_ID)
    if phase == "running":
        record["started_at"] = now


def start(note_dir, execution_id, phase):
    """A new record, replacing what an earlier run with this id left."""
    record = {"started_at": None, "done": False, "result": None}
    _enter(record, phase)
    return _write(note_dir, execution_id, record)


def update(note_dir, execution_id, phase=None, **fields):
    record = read(note_dir, execution_id) or {"started_at": None, "done": False, "result": None}
    if phase and phase != record.get("phase"):
        _enter(record, phase)
    record.update(fields)
    return _write(note_dir, execution_id, record)


def reporter(note_dir, execution_id):
    """report(phase) and report(field=value) for one run; does nothing for a run without an id.
    A phase reported again, as in a wait loop, is not written again."""
    last = None

    def report(phase=None, **fields):
        nonlocal last
        if not execution_id or (phase == last and not fields):
            return
        last = phase or last
        update(note_dir, execution_id, phase, **fields)

    return report


def active_runs(note_dir):
    """How many of the note's runs its own engine has not finished."""
    count = 0
    for path in (Path(note_dir) / KERNEL_DIR_REL).glob(f"{STATUS_PREFIX}*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not record.get("done") and record.get("phase") in POD_PHASES:
            count += 1
    return count


def remove_old(note_dir):
    cutoff = time.time() - KEEP_SECONDS
    for path in (Path(note_dir) / KERNEL_DIR_REL).glob(f"{STATUS_PREFIX}*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def read_output(note_dir, record, offset):
    """The run's console text from byte `offset`, and the offset to read from next."""
    if not record.get("run_dir"):
        return "", offset
    try:
        with open(os.path.join(note_dir, record["run_dir"], CONSOLE_FILE_NAME), "rb") as f:
            f.seek(offset)
            chunk = f.read(OUTPUT_CHUNK_BYTES)
    except OSError:
        return "", offset
    # A chunk may end inside a character; the rest of it comes with the next read.
    for cut in range(min(4, len(chunk))):
        try:
            return chunk[:len(chunk) - cut].decode("utf-8"), offset + len(chunk) - cut
        except UnicodeDecodeError:
            continue
    return chunk.decode("utf-8", errors="replace"), offset + len(chunk)
