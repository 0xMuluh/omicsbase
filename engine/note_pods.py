"""One pod per live note, for Kubernetes platforms (LUMI-K).

An engine started with NOTE_RUNNER=pods is a gateway. A note's first cell starts a pod for
that note from the gateway's own image, environment and volumes, and the gateway forwards
the note's cells to it. The pod is an ordinary engine that serves that one note, with
its own CPU and memory limit. A note idle for NOTE_IDLE_SECONDS has its pod deleted; the
engine in the pod saves the R session on shutdown, and the next pod restores it from the
shared volume.

With NOTE_RUNNER=workers the gateway runs notes in shared worker pods instead
(engine/note_workers.py), and uses this module's pods only for heavy notes.

Pod names come from the note id, so a note never has two pods: a new pod for a note
waits until the old one has finished saving and is gone.
"""
import hashlib
import http.client
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from engine.kernel.note_kernel import (
    IDLE_SECONDS, MAX_SESSIONS, OUT_OF_MEMORY_MESSAGE, QUEUE_SECONDS, REAP_INTERVAL_SECONDS,
)

logger = logging.getLogger("note_pods")

# The engine is a gateway: notes run in pods of their own (pods) or in shared workers (workers).
ENABLED = os.environ.get("NOTE_RUNNER", "local") in ("pods", "workers")
# A node that has not pulled the image yet needs a few minutes.
START_SECONDS = int(os.environ.get("NOTE_POD_START_SECONDS", "420"))
RESOURCES = {
    "requests": {
        "cpu": os.environ.get("NOTE_POD_CPU_REQUEST", "500m"),
        "memory": os.environ.get("NOTE_POD_MEMORY_REQUEST", "2Gi"),
    },
    "limits": {
        "cpu": os.environ.get("NOTE_POD_CPU_LIMIT", "2"),
        "memory": os.environ.get("NOTE_POD_MEMORY_LIMIT", "4Gi"),
    },
}
NOTE_LABEL = "omicsbase-note"
# On every pod that runs notes, note pods and workers alike; the NetworkPolicy selects it.
RUNNER_LABEL = "omicsbase-runner"
THREAD_ANNOTATION = "omicsbase/thread-id"
ENGINE_PORT = 8001


class PodsBusy(Exception):
    pass


class Cancelled(Exception):
    """The cell was cancelled while it waited for its pod."""


def pod_name(thread_id: str) -> str:
    return "note-" + hashlib.sha256(thread_id.encode()).hexdigest()[:20]


class Kube:
    """The few Kubernetes API calls the gateway needs, with the pod's service account."""

    _ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")

    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.namespace = (self._ACCOUNT / "namespace").read_text().strip()
        self.pods_url = f"https://{host}:{port}/api/v1/namespaces/{self.namespace}/pods"
        self.tls = ssl.create_default_context(cafile=str(self._ACCOUNT / "ca.crt"))

    def _call(self, method, url, body=None):
        # The token is rotated on disk, so read it for every call.
        token = (self._ACCOUNT / "token").read_text().strip()
        request = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, context=self.tls, timeout=30) as response:
            return json.loads(response.read() or b"{}")

    def get(self, name):
        try:
            return self._call("GET", f"{self.pods_url}/{name}")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise

    def create(self, manifest):
        """Returns the pod, or None if a pod of that name exists already."""
        try:
            return self._call("POST", self.pods_url, manifest)
        except urllib.error.HTTPError as err:
            if err.code == 409:
                return None
            if err.code == 403 and b"exceeded quota" in err.read():
                raise PodsBusy("The project's CPU or memory quota is used up.") from err
            raise

    def delete(self, name):
        try:
            self._call("DELETE", f"{self.pods_url}/{name}")
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise

    def list_pods(self, label):
        return self._call("GET", f"{self.pods_url}?labelSelector={label}").get("items", [])


def forward_cell(ip, payload, timeout_seconds, secret):
    """Run the cell in the note's pod. Without a timeout it waits as long as the pod takes,
    for example behind the note's earlier cells; keepalive probes end the wait if the pod
    is gone without closing the connection."""
    connection = http.client.HTTPConnection(ip, ENGINE_PORT, timeout=30)
    try:
        connection.connect()
        sock = connection.sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
        sock.settimeout(timeout_seconds)
        connection.request("POST", "/api/execute", body=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json", "X-Internal-Secret": secret})
        response = connection.getresponse()
        body = response.read()
        if response.status >= 400:
            raise http.client.HTTPException(f"note pod answered {response.status}")
        return json.loads(body)
    finally:
        connection.close()


SESSION_LOST_MESSAGE = ("The note's R session stopped while running this cell and was restarted. "
                        "Objects created since it was last saved may be gone.")


def run_forwarded(thread_id, acquire, send, lost):
    """Run a cell in the pod whose IP `acquire()` returns, with `send(ip)`, and turn what
    can go wrong into a message for the note. `lost()` explains a pod gone mid-cell."""
    try:
        ip = acquire()
    except Cancelled:
        return {"success": False, "cancelled": True, "error": "Cancelled",
                "markdown": "Execution cancelled before starting."}
    except PodsBusy as err:
        message = f"{err} Try again in a few minutes."
        return {"success": False, "error": message, "stdout": f"[Engine Busy]: {message}",
                "markdown": f"**Engine busy:** {message} R was not run."}
    except Exception:
        logger.exception("could not start the R session for note %s", thread_id)
        message = "The note's R session could not be started."
        return {"success": False, "error": message, "markdown": f"**{message}** R was not run."}
    try:
        return send(ip)
    except (OSError, http.client.HTTPException) as err:
        # A socket timeout (no errno) means the pod is there and has not answered yet,
        # for example a cell waiting behind a long one. Anything else: the pod went
        # away mid-cell or answered with an error.
        if isinstance(err, TimeoutError) and err.errno is None:
            message = ("The note's R session did not answer in time. It was not restarted; the cell "
                       "may still be running or waiting for an earlier cell to finish.")
            logger.warning("note %s did not answer in time", thread_id)
            return {"success": False, "error": message, "markdown": f"**{message}**"}
        return lost()


def pod_manifest(template, name, labels, env, resources, annotations=None):
    """A pod like the gateway's that runs the engine for notes. `env` replaces the
    gateway's variables of those names; a value of None leaves the variable out."""
    pod = json.loads(json.dumps(template))
    pod["metadata"] = {"name": name, "labels": {RUNNER_LABEL: "true", **labels}, "annotations": annotations or {}}
    container = pod["spec"]["containers"][0]
    kept = [e for e in container.get("env", []) if e["name"] not in env]
    container["env"] = kept + [{"name": k, "value": v} for k, v in env.items() if v is not None]
    container["resources"] = resources
    container["readinessProbe"] = {"tcpSocket": {"port": ENGINE_PORT}, "periodSeconds": 2}
    return pod


def _ready_ip(pod):
    status = (pod or {}).get("status", {})
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
    return status.get("podIP") if ready else None


@dataclass
class _Note:
    name: str
    last_used: float
    busy: int = 0
    # Cells that were running when this gateway took the pod over; part of `busy`.
    adopted: int = 0


class NotePods:
    def __init__(self, kube, template, secret, forward=forward_cell, clock=time.time, sleep=time.sleep,
                 max_pods=MAX_SESSIONS, idle_seconds=IDLE_SECONDS, queue_seconds=QUEUE_SECONDS,
                 start_seconds=START_SECONDS, active_runs=lambda thread_id: 0):
        self.kube, self.template, self.secret = kube, template, secret
        self.forward, self.clock, self.sleep = forward, clock, sleep
        self.max_pods, self.idle_seconds = max_pods, idle_seconds
        self.queue_seconds, self.start_seconds = queue_seconds, start_seconds
        # How many cells a note's pod has not finished, from their status files.
        self.active_runs = active_runs
        self.notes: dict[str, _Note] = {}
        self.lock = threading.Lock()
        self.starting: dict[str, threading.Lock] = {}

    def adopt_running(self):
        """Take over the note pods a previous gateway left running. A pod in the middle of
        a cell stays busy, so it is not stopped to make room or for being idle."""
        now = self.clock()
        for pod in self.kube.list_pods(NOTE_LABEL):
            meta = pod.get("metadata", {})
            thread_id = meta.get("annotations", {}).get(THREAD_ANNOTATION)
            if thread_id and not meta.get("deletionTimestamp"):
                running = self.active_runs(thread_id)
                self.notes[thread_id] = _Note(meta["name"], now, busy=running, adopted=running)
        if self.notes:
            logger.info("adopted %d running note pods", len(self.notes))

    def _release_adopted(self):
        with self.lock:
            adopted = [(t, n) for t, n in self.notes.items() if n.adopted]
        for thread_id, note in adopted:
            running = self.active_runs(thread_id)
            with self.lock:
                finished = max(0, note.adopted - running)
                if finished:
                    note.adopted -= finished
                    note.busy -= finished
                    note.last_used = self.clock()

    def serving(self, thread_id):
        """Whether the note has a pod that can still be running its cells."""
        with self.lock:
            note = self.notes.get(thread_id)
            if note and note.busy:
                return True
        pod = self.kube.get(pod_name(thread_id))
        return bool(pod) and not pod["metadata"].get("deletionTimestamp")

    def run_cell(self, thread_id, payload, timeout_seconds, on_phase=lambda phase: None,
                 cancelled=lambda: False):
        """timeout_seconds is how long to wait for the pod's answer; None for as long as it takes."""
        def send(ip):
            try:
                return self.forward(ip, payload, timeout_seconds, self.secret)
            finally:
                with self.lock:
                    note = self.notes.get(thread_id)
                    if note:
                        note.busy -= 1
                        note.last_used = self.clock()

        return run_forwarded(thread_id, lambda: self._acquire(thread_id, on_phase, cancelled), send,
                             lambda: self._session_lost(thread_id))

    def _session_lost(self, thread_id):
        pod = self.kube.get(pod_name(thread_id)) or {}
        statuses = pod.get("status", {}).get("containerStatuses") or [{}]
        reason = statuses[0].get("lastState", {}).get("terminated", {}).get("reason")
        if reason == "OOMKilled":
            message = OUT_OF_MEMORY_MESSAGE.format(limit=RESOURCES["limits"]["memory"])
        else:
            message = SESSION_LOST_MESSAGE
        logger.warning("note %s lost its R session (%s)", thread_id, reason or "unknown")
        return {"success": False, "error": message, "markdown": f"**{message}**"}

    def _acquire(self, thread_id, on_phase, cancelled):
        with self.lock:
            start_lock = self.starting.setdefault(thread_id, threading.Lock())
        with start_lock:
            with self.lock:
                note = self.notes.get(thread_id)
                if note:
                    note.busy += 1
            if note:
                ip = _ready_ip(self.kube.get(note.name))
                if ip:
                    return ip
                # Gone (evicted, deleted by hand) or not ready: start it again.
                with self.lock:
                    self.notes.pop(thread_id, None)
            self._make_room(thread_id, on_phase, cancelled)
            with self.lock:
                note = self.notes[thread_id] = _Note(pod_name(thread_id), self.clock(), busy=1)
            try:
                return self.start_pod(note.name, self._manifest(note.name, thread_id), on_phase, cancelled)
            except Cancelled:
                # The pod may be starting already: keep it for the note's next cell.
                with self.lock:
                    note.busy -= 1
                raise
            except BaseException:
                with self.lock:
                    self.notes.pop(thread_id, None)
                raise

    def _make_room(self, thread_id, on_phase, cancelled):
        deadline = self.clock() + self.queue_seconds
        while True:
            self._release_adopted()
            with self.lock:
                live = {t: n for t, n in self.notes.items() if t != thread_id}
                if len(live) < self.max_pods:
                    return
                idle = sorted((n.last_used, t) for t, n in live.items() if n.busy == 0)
                victim = live[idle[0][1]] if idle else None
                if victim:
                    del self.notes[idle[0][1]]
            if victim:
                logger.info("stopping least recently used note pod %s to make room", victim.name)
                self.kube.delete(victim.name)
                continue
            if self.clock() >= deadline:
                raise PodsBusy(f"All {self.max_pods} R sessions are busy running other notes' cells.")
            if cancelled():
                raise Cancelled()
            on_phase("waiting_for_slot")
            self.sleep(1)

    def start_pod(self, name, manifest, on_phase, cancelled):
        """Start pod `name` unless it runs already, and return its IP once it is ready."""
        # Pods being deleted still hold quota while they save their sessions.
        create_deadline = self.clock() + self.queue_seconds
        while True:
            existing = self.kube.get(name)
            if existing and not existing["metadata"].get("deletionTimestamp"):
                break  # running already, e.g. started before a gateway restart
            if not existing:
                try:
                    if self.kube.create(manifest) is not None:
                        logger.info("started pod %s", name)
                        break
                except PodsBusy:
                    if self.clock() >= create_deadline:
                        raise
            elif self.clock() >= create_deadline:
                raise PodsBusy("The note's previous R session is still being saved.")
            if cancelled():
                raise Cancelled()
            on_phase("waiting_for_slot")
            self.sleep(1)
        on_phase("starting_pod")
        deadline = self.clock() + self.start_seconds
        while self.clock() < deadline:
            ip = _ready_ip(self.kube.get(name))
            if ip:
                return ip
            if cancelled():
                raise Cancelled()
            self.sleep(2)
        self.kube.delete(name)
        raise PodsBusy("A new R session did not start in time.")

    def _manifest(self, name, thread_id):
        # The pod's own memory limit is the note's, so the session memory watchdog is off.
        env = {"NOTE_RUNNER": "local", "NOTE_MAX_SESSIONS": "1", "NOTE_SESSION_MEMORY_LIMIT": None}
        return pod_manifest(self.template, name, {NOTE_LABEL: "true"}, env, RESOURCES,
                            {THREAD_ANNOTATION: thread_id})

    def reap_idle(self):
        self._release_adopted()
        now = self.clock()
        with self.lock:
            stale = [(t, n) for t, n in self.notes.items()
                     if n.busy == 0 and now - n.last_used > self.idle_seconds]
            for thread_id, _ in stale:
                del self.notes[thread_id]
        for _, note in stale:
            logger.info("stopping idle note pod %s", note.name)
            self.kube.delete(note.name)
        return len(stale)


def template_from_own_pod(kube):
    """A note pod looks like the gateway's pod: same image, environment and volumes."""
    own = kube.get(os.environ["HOSTNAME"])
    spec = own["spec"]
    container = spec["containers"][0]
    keep = ("name", "image", "command", "args", "env", "envFrom", "ports")
    # Only the data volume: Kubernetes also mounts the gateway's API token, which a
    # note's R code must not get.
    volumes = [v for v in spec.get("volumes", []) if "persistentVolumeClaim" in v]
    names = {v["name"] for v in volumes}
    note = {k: v for k, v in container.items() if k in keep}
    note["volumeMounts"] = [m for m in container.get("volumeMounts", []) if m["name"] in names]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "spec": {
            "restartPolicy": "Always",
            "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": spec.get("terminationGracePeriodSeconds", 150),
            "containers": [note],
            "volumes": volumes,
        },
    }


_pods = None


def start(secret, active_runs):
    """Set up the gateway and its idle reaper."""
    kube = Kube()
    serve(NotePods(kube, template_from_own_pod(kube), secret, active_runs=active_runs))
    logger.info("note pods: max=%d idle=%ds", _pods.max_pods, _pods.idle_seconds)


def serve(gateway):
    """Take over the pods a previous gateway left running, then send cells to `gateway`
    (NotePods or NoteWorkers) and let it stop idle pods."""
    global _pods
    _pods = gateway
    gateway.adopt_running()

    def loop():
        while True:
            time.sleep(REAP_INTERVAL_SECONDS)
            try:
                gateway.reap_idle()
            except Exception:
                logger.exception("note pod reaper failed")

    threading.Thread(target=loop, name="note-pod-reaper", daemon=True).start()


def run_cell(thread_id, payload, timeout_seconds, on_phase, cancelled):
    return _pods.run_cell(thread_id, payload, timeout_seconds, on_phase, cancelled)


def serving(thread_id):
    return _pods.serving(thread_id)
