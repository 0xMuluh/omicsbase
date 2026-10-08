"""The note-pod gateway's decisions, against a fake Kubernetes API and a fake clock."""
import unittest
import unittest.mock

from engine import note_pods
from engine.note_pods import NotePods, PodsBusy, pod_name

TEMPLATE = {
    "apiVersion": "v1",
    "kind": "Pod",
    "spec": {
        "containers": [{
            "name": "engine",
            "image": "registry/engine:tag",
            "env": [{"name": "BASE_URL", "value": "https://engine"}, {"name": "NOTE_RUNNER", "value": "pods"},
                    {"name": "NOTE_MAX_SESSIONS", "value": "5"}],
        }],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}],
    },
}


class FakeKube:
    """Pods are ready at once; a deleted pod stays (terminating) for `save_seconds`."""

    def __init__(self, clock, save_seconds=0):
        self.clock, self.save_seconds = clock, save_seconds
        self.pods, self.created, self.deleted = {}, [], []
        self.quota_full_until = 0

    def _expire(self):
        for name, pod in list(self.pods.items()):
            gone_at = pod.get("gone_at")
            if gone_at is not None and self.clock() >= gone_at:
                del self.pods[name]

    def get(self, name):
        self._expire()
        pod = self.pods.get(name)
        if not pod:
            return None
        meta = dict(pod["metadata"])
        if "gone_at" in pod:
            meta["deletionTimestamp"] = "now"
        status = {"podIP": f"10.0.0.{len(name)}", "conditions": [{"type": "Ready", "status": "True"}]}
        if pod.get("last_reason"):
            status["containerStatuses"] = [{"lastState": {"terminated": {"reason": pod["last_reason"]}}}]
        return {"metadata": meta, "status": status}

    def create(self, manifest):
        self._expire()
        name = manifest["metadata"]["name"]
        if name in self.pods:
            return None
        if self.clock() < self.quota_full_until:
            raise PodsBusy("The project's CPU or memory quota is used up.")
        self.pods[name] = {"metadata": manifest["metadata"], "manifest": manifest}
        self.created.append(name)
        return manifest

    def delete(self, name):
        self.deleted.append(name)
        if name in self.pods:
            self.pods[name]["gone_at"] = self.clock() + self.save_seconds

    def list_notes(self):
        self._expire()
        return [{"metadata": p["metadata"]} for p in self.pods.values()]


class NotePodTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.kube = FakeKube(lambda: self.now)
        self.forwarded = []
        self.pods = self.make()

    def make(self, **kwargs):
        options = dict(max_pods=2, idle_seconds=1200, queue_seconds=120, start_seconds=300)
        options.update(kwargs)
        return NotePods(self.kube, TEMPLATE, "secret", forward=self.forward, clock=lambda: self.now,
                        sleep=self.sleep, **options)

    def sleep(self, seconds):
        self.now += seconds

    def forward(self, ip, payload, timeout, secret):
        self.forwarded.append((ip, payload["thread_id"], secret))
        return {"success": True, "markdown": "ok"}

    def cell(self, thread_id):
        return self.pods.run_cell(thread_id, {"code": "1", "thread_id": thread_id}, 600)

    def test_first_cell_starts_the_note_pod_and_later_cells_reuse_it(self):
        self.assertTrue(self.cell("a")["success"])
        self.assertTrue(self.cell("a")["success"])
        self.assertEqual(self.kube.created, [pod_name("a")])
        self.assertEqual([f[1] for f in self.forwarded], ["a", "a"])
        self.assertEqual(self.forwarded[0][2], "secret")

    def test_note_pod_is_the_gateway_pod_serving_one_note_with_its_own_limits(self):
        self.cell("a")
        manifest = self.kube.pods[pod_name("a")]["manifest"]
        container = manifest["spec"]["containers"][0]
        env = {e["name"]: e["value"] for e in container["env"]}
        self.assertEqual(env, {"BASE_URL": "https://engine", "NOTE_RUNNER": "local", "NOTE_MAX_SESSIONS": "1"})
        self.assertEqual(container["image"], "registry/engine:tag")
        self.assertEqual(container["resources"], note_pods.RESOURCES)
        self.assertEqual(manifest["metadata"]["annotations"][note_pods.THREAD_ANNOTATION], "a")
        self.assertEqual(manifest["spec"]["volumes"], TEMPLATE["spec"]["volumes"])

    def test_full_gateway_stops_the_least_recently_used_idle_note(self):
        self.cell("a")
        self.now += 10
        self.cell("b")
        self.now += 10
        self.cell("a")
        self.cell("c")
        self.assertEqual(self.kube.deleted, [pod_name("b")])
        self.assertEqual(set(self.pods.notes), {"a", "c"})

    def test_all_notes_busy_means_the_cell_waits_then_reports_busy(self):
        self.cell("a")
        self.cell("b")
        for note in self.pods.notes.values():
            note.busy = 1
        result = self.cell("c")
        self.assertFalse(result["success"])
        self.assertIn("busy", result["markdown"])
        self.assertGreaterEqual(self.now, 1000 + 120)
        self.assertNotIn(pod_name("c"), self.kube.created)
        self.assertNotIn("c", self.pods.notes)

    def test_new_pod_waits_until_the_old_pod_has_saved_the_session(self):
        self.kube.save_seconds = 40
        self.cell("a")
        self.now += 2000
        self.assertEqual(self.pods.reap_idle(), 1)
        started = self.now
        self.cell("a")
        self.assertGreaterEqual(self.now, started + 40)
        self.assertEqual(self.kube.created, [pod_name("a"), pod_name("a")])

    def test_full_quota_is_waited_out(self):
        self.kube.quota_full_until = self.now + 30
        self.assertTrue(self.cell("a")["success"])
        self.assertGreaterEqual(self.now, 1030)

    def test_quota_that_stays_full_reports_busy(self):
        self.kube.quota_full_until = self.now + 10_000
        result = self.cell("a")
        self.assertFalse(result["success"])
        self.assertIn("quota", result["markdown"])
        self.assertNotIn("a", self.pods.notes)

    def test_idle_notes_are_stopped_but_busy_ones_kept(self):
        self.cell("a")
        self.cell("b")
        self.pods.notes["b"].busy = 1
        self.now += 1201
        self.assertEqual(self.pods.reap_idle(), 1)
        self.assertEqual(self.kube.deleted, [pod_name("a")])
        self.assertEqual(set(self.pods.notes), {"b"})

    def test_pod_that_vanished_is_started_again(self):
        self.cell("a")
        del self.kube.pods[pod_name("a")]
        self.assertTrue(self.cell("a")["success"])
        self.assertEqual(self.kube.created, [pod_name("a"), pod_name("a")])

    def test_restarted_gateway_adopts_running_note_pods(self):
        self.cell("a")
        fresh = self.make()
        fresh.adopt_running()
        self.assertEqual(set(fresh.notes), {"a"})
        fresh.run_cell("a", {"code": "1", "thread_id": "a"}, 600)
        self.assertEqual(self.kube.created, [pod_name("a")])

    def test_note_pod_template_leaves_out_the_gateway_api_token(self):
        gateway = {"spec": {
            "terminationGracePeriodSeconds": 150,
            "containers": [{
                "name": "engine", "image": "img", "command": ["python3"], "resources": {"limits": {"cpu": "1"}},
                "volumeMounts": [{"name": "data", "mountPath": "/data"},
                                 {"name": "kube-api-access-x", "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount"}],
            }],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "c"}},
                        {"name": "kube-api-access-x", "projected": {}}],
        }}

        class OwnPod:
            def get(self, name):
                return gateway

        with unittest.mock.patch.dict("os.environ", {"HOSTNAME": "gateway-pod"}):
            spec = note_pods.template_from_own_pod(OwnPod())["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["volumes"], [{"name": "data", "persistentVolumeClaim": {"claimName": "c"}}])
        self.assertEqual(spec["containers"][0]["volumeMounts"], [{"name": "data", "mountPath": "/data"}])
        self.assertNotIn("resources", spec["containers"][0])

    def test_kubernetes_failure_reaches_the_note_as_a_message(self):
        def broken(manifest):
            raise RuntimeError("API down")

        self.kube.create = broken
        with self.assertLogs("note_pods", level="ERROR"):
            result = self.cell("a")
        self.assertFalse(result["success"])
        self.assertIn("could not be started", result["markdown"])
        self.assertNotIn("a", self.pods.notes)

    def test_note_that_ran_out_of_memory_is_told_so(self):
        self.cell("a")

        def killed(ip, payload, timeout, secret):
            self.kube.pods[pod_name("a")]["last_reason"] = "OOMKilled"
            raise ConnectionResetError("connection reset by peer")

        self.forward = killed
        self.pods.forward = killed
        with self.assertLogs("note_pods", level="WARNING"):
            result = self.cell("a")
        self.assertFalse(result["success"])
        self.assertIn("ran out of memory (limit 4Gi)", result["markdown"])
        self.assertEqual(self.pods.notes["a"].busy, 0)

    def test_session_lost_for_another_reason_is_reported_plainly(self):
        self.cell("a")

        def dropped(ip, payload, timeout, secret):
            raise TimeoutError("timed out")

        self.pods.forward = dropped
        with self.assertLogs("note_pods", level="WARNING"):
            result = self.cell("a")
        self.assertIn("stopped while running this cell", result["markdown"])

    def test_pod_names_are_valid_and_stable(self):
        name = pod_name("6f1c2b0e-3d4a-4b5c-9e8f-0a1b2c3d4e5f")
        self.assertEqual(name, pod_name("6f1c2b0e-3d4a-4b5c-9e8f-0a1b2c3d4e5f"))
        self.assertRegex(name, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
        self.assertLessEqual(len(name), 63)
        self.assertNotEqual(name, pod_name("other"))


if __name__ == "__main__":
    unittest.main()
