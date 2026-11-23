"""Memory held by the R sessions of this engine, and a watchdog over it.

A shared worker (engine/note_workers.py) hosts many notes' sessions in one pod. The
gateway asks it which notes it hosts and how much memory each holds, and the watchdog
stops a session that passes NOTE_SESSION_MEMORY_LIMIT before the pod's own limit is
reached; the note is told why, as a note pod that ran out of memory tells it.
"""
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from engine.kernel import note_kernel

logger = logging.getLogger("session_memory")

# Unset: no watchdog (a pod of one note has the pod's own limit).
LIMIT = os.environ.get("NOTE_SESSION_MEMORY_LIMIT", "")
WATCH_SECONDS = 3
_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
          "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}


def parse_bytes(quantity):
    """Bytes in a Kubernetes memory quantity such as 4Gi or 500M."""
    number, unit = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", quantity.strip()).groups()
    return int(float(number) * _UNITS[unit])


def processes():
    """Parent pid and resident bytes of every process this pod can see, by pid."""
    table = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            status = Path(entry.path, "status").read_text()
        except OSError:
            continue  # exited meanwhile
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        rss_kb = int(fields.get("VmRSS", "0").split()[0])
        table[int(entry.name)] = (int(fields["PPid"]), rss_kb * 1024)
    return table


def session_rss(table):
    """Resident bytes of each live session: its R process and everything R started."""
    children = defaultdict(list)
    for pid, (parent, _) in table.items():
        children[parent].append(pid)

    def tree(pid):
        return table.get(pid, (0, 0))[1] + sum(tree(child) for child in children[pid])

    return {handle: tree(handle.pid) for handle in note_kernel._live_kernels()}


def report(projects_dir, read=processes):
    """The notes this engine hosts and the memory each holds, for the gateway."""
    return [{"thread_id": os.path.relpath(handle.project_dir, projects_dir), "rss_bytes": rss}
            for handle, rss in session_rss(read()).items()]


def stop_over_limit(limit, read=processes):
    """Stop, unsaved, each session holding more than `limit` (a quantity such as 4Gi).
    Its running cell ends with the out-of-memory message."""
    limit_bytes = parse_bytes(limit)
    stopped = []
    for handle, rss in session_rss(read()).items():
        if rss > limit_bytes:
            logger.warning("note session over %s (%d MiB), stopping it: %s", limit, rss >> 20, handle.project_dir)
            handle.out_of_memory = limit
            note_kernel.kill_kernel(handle)
            stopped.append(handle)
    return stopped


def start_watchdog():
    def loop():
        while True:
            time.sleep(WATCH_SECONDS)
            try:
                stop_over_limit(LIMIT)
            except Exception:
                logger.exception("session memory watchdog failed")

    threading.Thread(target=loop, name="session-memory-watchdog", daemon=True).start()
    logger.info("session memory watchdog: limit=%s every %ds", LIMIT, WATCH_SECONDS)
