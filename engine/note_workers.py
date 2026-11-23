"""Shared worker pods: many notes' R sessions per pod, for Kubernetes platforms (LUMI-K).

An engine started with NOTE_RUNNER=workers is a gateway, as with NOTE_RUNNER=pods, but
notes run in at most NOTE_WORKERS worker pods. A worker is started from the gateway's own
pod spec like a note pod, with a size of its own (NOTE_WORKER_*), and is an ordinary
engine hosting up to NOTE_WORKER_SESSIONS R sessions: it stops its least recently used
idle session to make room, saves and stops sessions idle for NOTE_IDLE_SECONDS, and
stops a session holding more than NOTE_SESSION_MEMORY_LIMIT (engine/kernel/session_memory.py).

A note's cells go to the worker hosting its session. A note without one goes to the
worker with the most free memory among those with a free session; when none has one, a
new worker is started, and at NOTE_WORKERS the fullest are shared and evict idle sessions.
Workers say which notes they host (/api/sessions), so a restarted gateway finds every
note again. A worker that has hosted nothing for NOTE_IDLE_SECONDS is deleted, but
NOTE_WORKERS_MIN workers keep running, since a new one takes minutes to start.

A note whose session passed NOTE_HEAVY_SESSION_MEMORY, or was stopped for its memory, is
heavy: its next session runs in a pod of its own (engine/note_pods.py, NOTE_POD_* size, at
most NOTE_HEAVY_PODS at once). The gateway keeps heavy marks in memory only: after a
restart, only notes still in pods of their own are known to be heavy.
"""
import http.client
import json
import logging
import os
import threading
import urllib.request
from dataclasses import dataclass, field

from engine.kernel.note_kernel import IDLE_SECONDS
from engine.kernel.session_memory import parse_bytes
from engine.note_pods import (
    ENGINE_PORT, SESSION_LOST_MESSAGE, Kube, NotePods, PodsBusy, _ready_ip, forward_cell, pod_manifest,
    run_forwarded, serve, template_from_own_pod,
)

logger = logging.getLogger("note_workers")

ENABLED = os.environ.get("NOTE_RUNNER") == "workers"
MAX_WORKERS = int(os.environ.get("NOTE_WORKERS", "2"))
MIN_WORKERS = int(os.environ.get("NOTE_WORKERS_MIN", "1"))
SESSIONS = int(os.environ.get("NOTE_WORKER_SESSIONS", "12"))
RESOURCES = {
    "requests": {
        "cpu": os.environ.get("NOTE_WORKER_CPU_REQUEST", "1"),
        "memory": os.environ.get("NOTE_WORKER_MEMORY_REQUEST", "8Gi"),
    },
    "limits": {
        "cpu": os.environ.get("NOTE_WORKER_CPU_LIMIT", "4"),
        "memory": os.environ.get("NOTE_WORKER_MEMORY_LIMIT", "16Gi"),
    },
}
HEAVY_BYTES = parse_bytes(os.environ.get("NOTE_HEAVY_SESSION_MEMORY", "3Gi"))
HEAVY_PODS = int(os.environ.get("NOTE_HEAVY_PODS", "1"))
WORKER_LABEL = "omicsbase-worker"


def worker_name(index):
    return f"notes-worker-{index}"


def fetch_sessions(ip, secret):
    """The notes a worker hosts, with the resident bytes of each one's session."""
    request = urllib.request.Request(f"http://{ip}:{ENGINE_PORT}/api/sessions",
                                     headers={"X-Internal-Secret": secret})
    with urllib.request.urlopen(request, timeout=10) as response:
        return {s["thread_id"]: s["rss_bytes"] for s in json.loads(response.read())["sessions"]}


@dataclass
class _Worker:
    name: str
    ip: str | None = None
    # Resident bytes of each note's session, as the worker last reported them.
    sessions: dict = field(default_factory=dict)
    empty_since: float | None = None


class NoteWorkers:
    def __init__(self, kube, template, secret, pods, forward=forward_cell, fetch=fetch_sessions,
                 max_workers=MAX_WORKERS, min_workers=MIN_WORKERS, sessions_per_worker=SESSIONS,
                 idle_seconds=IDLE_SECONDS, heavy_bytes=HEAVY_BYTES):
        self.kube, self.template, self.secret = kube, template, secret
        # Runs heavy notes in pods of their own, and starts workers the same way.
        self.pods = pods
        self.forward, self.fetch = forward, fetch
        self.max_workers, self.min_workers = max_workers, min_workers
        self.sessions_per_worker, self.idle_seconds, self.heavy_bytes = sessions_per_worker, idle_seconds, heavy_bytes
        self.workers: dict[str, _Worker] = {}
        self.placed: dict[str, str] = {}  # note -> worker hosting or about to host its session
        self.busy: dict[str, int] = {}  # cells this gateway is forwarding, by note
        self.heavy: set[str] = set()
        self.lock = threading.Lock()

    def adopt_running(self):
        """Take over the workers and heavy notes' pods a previous gateway left running."""
        self.pods.adopt_running()
        self.heavy.update(self.pods.notes)
        for pod in self.kube.list_pods(WORKER_LABEL):
            meta = pod["metadata"]
            if not meta.get("deletionTimestamp"):
                self.workers[meta["name"]] = _Worker(meta["name"])
        self.refresh()
        self._keep_warm()
        logger.info("note workers: %d running, hosting %d notes", len(self.workers), len(self.placed))

    def run_cell(self, thread_id, payload, timeout_seconds, on_phase=lambda phase: None,
                 cancelled=lambda: False):
        if thread_id in self.heavy:
            self.refresh()  # whether a worker still hosts its session
        with self.lock:
            own_pod = thread_id in self.heavy and thread_id not in self.placed
            if not own_pod:
                self.busy[thread_id] = self.busy.get(thread_id, 0) + 1
        if own_pod:
            return self.pods.run_cell(thread_id, payload, timeout_seconds, on_phase, cancelled)
        try:
            result = run_forwarded(
                thread_id, lambda: self._place(thread_id, on_phase, cancelled),
                lambda ip: self.forward(ip, payload, timeout_seconds, self.secret),
                lambda: self._lost(thread_id))
        finally:
            with self.lock:
                self.busy[thread_id] -= 1
                if not self.busy[thread_id]:
                    del self.busy[thread_id]
        if result.get("out_of_memory"):
            with self.lock:
                self.heavy.add(thread_id)
                self.placed.pop(thread_id, None)
            logger.info("note %s ran out of memory in a worker; it gets a pod of its own", thread_id)
        return result

    def serving(self, thread_id):
        """Whether the note has a pod that can still be running its cells."""
        with self.lock:
            if self.busy.get(thread_id):
                return True
            name = self.placed.get(thread_id)
        if name is None:
            return self.pods.serving(thread_id)
        pod = self.kube.get(name)
        return bool(pod) and not pod["metadata"].get("deletionTimestamp")

    def _lost(self, thread_id):
        logger.warning("note %s lost its R session in worker %s", thread_id, self.placed.get(thread_id))
        return {"success": False, "error": SESSION_LOST_MESSAGE, "markdown": f"**{SESSION_LOST_MESSAGE}**"}

    def _place(self, thread_id, on_phase, cancelled):
        """The IP of the worker for the note, started if need be."""
        if thread_id not in self.placed:
            self.refresh()
        name = self._choose(thread_id)
        return _ready_ip(self.kube.get(name)) or self.pods.start_pod(name, self._manifest(name), on_phase, cancelled)

    def _choose(self, thread_id):
        with self.lock:
            if thread_id in self.placed:
                return self.placed[thread_id]
            hosted = {name: set(worker.sessions) for name, worker in self.workers.items()}
            for note, name in self.placed.items():
                hosted[name].add(note)
            room = [w for w in self.workers.values() if len(hosted[w.name]) < self.sessions_per_worker]
            if not room and len(self.workers) < self.max_workers:
                name = next(n for n in map(worker_name, range(self.max_workers)) if n not in self.workers)
                self.workers[name] = _Worker(name)
                logger.info("every worker is full; starting %s", name)
            else:
                # Running workers first; all have the same limit, so the least used has the
                # most free memory. With no room anywhere the worker evicts an idle session.
                name = max(room or self.workers.values(),
                           key=lambda w: (w.ip is not None, -sum(w.sessions.values()))).name
            self.placed[thread_id] = name
            return name

    def refresh(self):
        """Ask each worker which notes it hosts and how much memory each holds. A note that
        a worker no longer hosts, with no cell on its way, is no longer placed there."""
        with self.lock:
            names = list(self.workers)
        for name in names:
            pod = self.kube.get(name)
            ip = _ready_ip(pod)
            if pod and not ip:
                continue  # starting or restarting: keep what it last reported
            try:
                sessions = self.fetch(ip, self.secret) if ip else {}
            except (OSError, http.client.HTTPException, ValueError, KeyError):
                logger.warning("worker %s did not report its sessions", name)
                continue
            with self.lock:
                worker = self.workers.get(name)
                if worker is None:
                    continue  # stopped meanwhile
                worker.ip, worker.sessions = ip, sessions
                for note, rss in sessions.items():
                    self.placed[note] = name
                    if rss > self.heavy_bytes and note not in self.heavy:
                        logger.info("note %s holds %d MiB; its next session gets a pod of its own", note, rss >> 20)
                        self.heavy.add(note)
                for note in [n for n, w in self.placed.items()
                             if w == name and n not in sessions and not self.busy.get(n)]:
                    del self.placed[note]

    def reap_idle(self):
        """Stop heavy notes' idle pods and workers that have hosted nothing for idle_seconds."""
        stopped = self.pods.reap_idle()
        self.refresh()
        now = self.pods.clock()
        warm = set(map(worker_name, range(self.min_workers)))
        with self.lock:
            in_use = set(self.placed.values())
            idle = []
            for name, worker in self.workers.items():
                if name in in_use or name in warm:
                    worker.empty_since = None
                elif worker.empty_since is None:
                    worker.empty_since = now
                elif now - worker.empty_since > self.idle_seconds:
                    idle.append(name)
            for name in idle:
                del self.workers[name]
        for name in idle:
            logger.info("stopping idle worker %s", name)
            self.kube.delete(name)
        self._keep_warm()
        return stopped + len(idle)

    def _keep_warm(self):
        """Start the first min_workers workers if they are not running, without waiting."""
        for name in map(worker_name, range(self.min_workers)):
            with self.lock:
                self.workers.setdefault(name, _Worker(name))
            if self.kube.get(name) is None:
                try:
                    self.kube.create(self._manifest(name))
                    logger.info("started worker %s to keep it warm", name)
                except PodsBusy:
                    logger.warning("no quota left to keep worker %s warm", name)

    def _manifest(self, name):
        env = {"NOTE_RUNNER": "local", "NOTE_MAX_SESSIONS": str(self.sessions_per_worker)}
        return pod_manifest(self.template, name, {WORKER_LABEL: "true"}, env, RESOURCES)


def start(secret, active_runs):
    """Set up the gateway, its workers and the pods of heavy notes."""
    kube = Kube()
    template = template_from_own_pod(kube)
    pods = NotePods(kube, template, secret, max_pods=HEAVY_PODS, active_runs=active_runs)
    serve(NoteWorkers(kube, template, secret, pods))
    logger.info("note workers: max=%d min=%d sessions=%d heavy pods=%d",
                MAX_WORKERS, MIN_WORKERS, SESSIONS, HEAVY_PODS)
