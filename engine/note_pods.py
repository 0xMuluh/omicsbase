"""One pod per live note, for Kubernetes platforms (LUMI-K).

An engine started with NOTE_RUNNER=pods is a gateway. A note's first cell starts a pod for
that note from the gateway's own image, environment and volumes, and the gateway forwards
the note's cells to it. The pod is an ordinary engine that serves that one note, with
its own CPU and memory limit. A note idle for NOTE_IDLE_SECONDS has its pod deleted; the
engine in the pod saves the R session on shutdown, and the next pod restores it from the
shared volume.

Pod names come from the note id, so a note never has two pods: a new pod for a note
waits until the old one has finished saving and is gone.
"""
import hashlib
import http.client
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from engine.kernel.note_kernel import IDLE_SECONDS, MAX_SESSIONS, QUEUE_SECONDS, REAP_INTERVAL_SECONDS

logger = logging.getLogger("note_pods")

ENABLED = os.environ.get("NOTE_RUNNER", "local") == "pods"
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
THREAD_ANNOTATION = "omicsbase/thread-id"
ENGINE_PORT = 8001


class PodsBusy(Exception):
    pass


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

    def list_notes(self):
        return self._call("GET", f"{self.pods_url}?labelSelector={NOTE_LABEL}").get("items", [])


def forward_cell(ip, payload, timeout_seconds, secret):
    request = urllib.request.Request(
        f"http://{ip}:{ENGINE_PORT}/api/execute", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Internal-Secret": secret}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds + 60) as response:
        return json.loads(response.read())


def _ready_ip(pod):
    status = (pod or {}).get("status", {})
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
    return status.get("podIP") if ready else None


@dataclass
class _Note:
    name: str
    last_used: float
    busy: int = 0


class NotePods:
    def __init__(self, kube, template, secret, forward=forward_cell, clock=time.time, sleep=time.sleep,
                 max_pods=MAX_SESSIONS, idle_seconds=IDLE_SECONDS, queue_seconds=QUEUE_SECONDS,
                 start_seconds=START_SECONDS):
        self.kube, self.template, self.secret = kube, template, secret
        self.forward, self.clock, self.sleep = forward, clock, sleep
        self.max_pods, self.idle_seconds = max_pods, idle_seconds
        self.queue_seconds, self.start_seconds = queue_seconds, start_seconds
        self.notes: dict[str, _Note] = {}
        self.lock = threading.Lock()
        self.starting: dict[str, threading.Lock] = {}

    def adopt_running(self):
        """Take over the note pods a previous gateway left running."""
        now = self.clock()
        for pod in self.kube.list_notes():
            meta = pod.get("metadata", {})
            thread_id = meta.get("annotations", {}).get(THREAD_ANNOTATION)
            if thread_id and not meta.get("deletionTimestamp"):
                self.notes[thread_id] = _Note(meta["name"], now)
        if self.notes:
            logger.info("adopted %d running note pods", len(self.notes))

    def run_cell(self, thread_id, payload, timeout_seconds):
        try:
            ip = self._acquire(thread_id)
        except PodsBusy as err:
            message = f"{err} Try again in a few minutes."
            return {"success": False, "error": message, "stdout": f"[Engine Busy]: {message}",
                    "markdown": f"**Engine busy:** {message} R was not run."}
        except Exception:
            logger.exception("could not start the R session for note %s", thread_id)
            message = "The note's R session could not be started."
            return {"success": False, "error": message, "markdown": f"**{message}** R was not run."}
        try:
            return self.forward(ip, payload, timeout_seconds, self.secret)
        except (OSError, http.client.HTTPException):
            # urllib's errors are OSErrors: the pod went away mid-cell or answered with an error.
            return self._session_lost(thread_id)
        finally:
            with self.lock:
                note = self.notes.get(thread_id)
                if note:
                    note.busy -= 1
                    note.last_used = self.clock()

    def _session_lost(self, thread_id):
        pod = self.kube.get(pod_name(thread_id)) or {}
        statuses = pod.get("status", {}).get("containerStatuses") or [{}]
        reason = statuses[0].get("lastState", {}).get("terminated", {}).get("reason")
        if reason == "OOMKilled":
            message = (f"The note's R session ran out of memory (limit {RESOURCES['limits']['memory']}) "
                       "and was restarted. Objects created since it was last saved are gone; "
                       "rerun the earlier cells, with less data in memory.")
        else:
            message = ("The note's R session stopped while running this cell and was restarted. "
                       "Objects created since it was last saved may be gone.")
        logger.warning("note %s lost its R session (%s)", thread_id, reason or "unknown")
        return {"success": False, "error": message, "markdown": f"**{message}**"}

    def _acquire(self, thread_id):
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
            self._make_room(thread_id)
            with self.lock:
                self.notes[thread_id] = _Note(pod_name(thread_id), self.clock(), busy=1)
            try:
                return self._start(thread_id)
            except BaseException:
                with self.lock:
                    self.notes.pop(thread_id, None)
                raise

    def _make_room(self, thread_id):
        deadline = self.clock() + self.queue_seconds
        while True:
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
            self.sleep(1)

    def _start(self, thread_id):
        name = pod_name(thread_id)
        manifest = self._manifest(name, thread_id)
        # Pods being deleted still hold quota while they save their sessions.
        create_deadline = self.clock() + self.queue_seconds
        while True:
            existing = self.kube.get(name)
            if existing and not existing["metadata"].get("deletionTimestamp"):
                break  # running already, e.g. started before a gateway restart
            if not existing:
                try:
                    if self.kube.create(manifest) is not None:
                        logger.info("started note pod %s", name)
                        break
                except PodsBusy:
                    if self.clock() >= create_deadline:
                        raise
            elif self.clock() >= create_deadline:
                raise PodsBusy("The note's previous R session is still being saved.")
            self.sleep(1)
        deadline = self.clock() + self.start_seconds
        while self.clock() < deadline:
            ip = _ready_ip(self.kube.get(name))
            if ip:
                return ip
            self.sleep(2)
        self.kube.delete(name)
        raise PodsBusy("A new R session did not start in time.")

    def _manifest(self, name, thread_id):
        pod = json.loads(json.dumps(self.template))
        pod["metadata"] = {"name": name, "labels": {NOTE_LABEL: "true"},
                           "annotations": {THREAD_ANNOTATION: thread_id}}
        container = pod["spec"]["containers"][0]
        env = [e for e in container.get("env", []) if e["name"] not in ("NOTE_RUNNER", "NOTE_MAX_SESSIONS")]
        container["env"] = env + [{"name": "NOTE_RUNNER", "value": "local"},
                                  {"name": "NOTE_MAX_SESSIONS", "value": "1"}]
        container["resources"] = RESOURCES
        container["readinessProbe"] = {"tcpSocket": {"port": ENGINE_PORT}, "periodSeconds": 2}
        return pod

    def reap_idle(self):
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


def start(secret):
    """Set up the gateway and its idle reaper."""
    global _pods
    kube = Kube()
    _pods = NotePods(kube, template_from_own_pod(kube), secret)
    _pods.adopt_running()

    def loop():
        while True:
            time.sleep(REAP_INTERVAL_SECONDS)
            try:
                _pods.reap_idle()
            except Exception:
                logger.exception("note pod reaper failed")

    threading.Thread(target=loop, name="note-pod-reaper", daemon=True).start()
    logger.info("note pods: max=%d idle=%ds", _pods.max_pods, _pods.idle_seconds)


def run_cell(thread_id, payload, timeout_seconds):
    return _pods.run_cell(thread_id, payload, timeout_seconds)
