"""Shared workers: the gateway's placement against a fake Kubernetes API, and the worker's
memory watchdog and saving after cells against fake processes. No R."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import note_workers, server
from engine.kernel import executor, note_kernel, session_memory
from engine.note_pods import NOTE_LABEL, RUNNER_LABEL, NotePods, pod_name
from engine.note_workers import WORKER_LABEL, NoteWorkers, worker_name
from engine.tests.test_note_pods import TEMPLATE, FakeKube

GiB = 2**30
W0, W1 = worker_name(0), worker_name(1)


class WorkerKube(FakeKube):
    """A pod's IP is its name, so the fake workers can be told apart."""

    def get(self, name):
        pod = super().get(name)
        if pod:
            pod["status"]["podIP"] = name
        return pod


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.kube = WorkerKube(lambda: self.now)
        # What each worker reports: note -> resident bytes.
        self.reported = {}
        self.forwarded = []
        self.workers = self.make()

    def make(self, template=TEMPLATE, **kwargs):
        pods = NotePods(self.kube, template, "secret", forward=self.forward, clock=lambda: self.now,
                        sleep=self.sleep, max_pods=1, idle_seconds=1200, queue_seconds=120, start_seconds=300)
        options = dict(max_workers=2, min_workers=0, sessions_per_worker=2, idle_seconds=1200, heavy_bytes=3 * GiB)
        options.update(kwargs)
        return NoteWorkers(self.kube, template, "secret", pods, forward=self.forward, fetch=self.fetch, **options)

    def sleep(self, seconds):
        self.now += seconds

    def forward(self, ip, payload, timeout, secret):
        """The pod at `ip` runs the cell; a worker then hosts the note's session, at 1 GiB."""
        self.forwarded.append((ip, payload["thread_id"]))
        if ip.startswith("notes-worker"):
            self.reported.setdefault(ip, {}).setdefault(payload["thread_id"], GiB)
        return {"success": True}

    def fetch(self, ip, secret):
        self.assertEqual(secret, "secret")
        return dict(self.reported.get(ip, {}))

    def cell(self, thread_id, workers=None):
        return (workers or self.workers).run_cell(thread_id, {"code": "1", "thread_id": thread_id}, 600)

    def ran_in(self):
        return self.forwarded[-1][0]

    def test_first_cell_starts_a_worker_that_later_notes_share(self):
        self.cell("a")
        self.cell("b")
        self.assertEqual(self.kube.created, [W0])
        self.assertEqual(self.forwarded, [(W0, "a"), (W0, "b")])

    def test_worker_runs_the_engine_for_many_notes_with_a_size_of_its_own(self):
        self.cell("a")
        manifest = self.kube.pods[W0]["manifest"]
        container = manifest["spec"]["containers"][0]
        env = {e["name"]: e["value"] for e in container["env"]}
        self.assertEqual(env, {"BASE_URL": "https://engine", "NOTE_RUNNER": "local", "NOTE_MAX_SESSIONS": "2"})
        self.assertEqual(container["resources"], note_workers.RESOURCES)
        self.assertEqual(manifest["metadata"]["labels"], {RUNNER_LABEL: "true", WORKER_LABEL: "true"})
        self.assertEqual(manifest["spec"]["volumes"], TEMPLATE["spec"]["volumes"])

    def test_session_memory_limit_applies_in_workers_but_not_in_a_heavy_notes_own_pod(self):
        template = json.loads(json.dumps(TEMPLATE))
        template["spec"]["containers"][0]["env"].append({"name": "NOTE_SESSION_MEMORY_LIMIT", "value": "4Gi"})
        workers = self.make(template)
        self.cell("a", workers)
        workers.heavy.add("b")
        self.cell("b", workers)
        env = lambda name: {e["name"] for e in self.kube.pods[name]["manifest"]["spec"]["containers"][0]["env"]}
        self.assertIn("NOTE_SESSION_MEMORY_LIMIT", env(W0))
        self.assertNotIn("NOTE_SESSION_MEMORY_LIMIT", env(pod_name("b")))
        self.assertEqual(self.kube.pods[pod_name("b")]["manifest"]["metadata"]["labels"],
                         {RUNNER_LABEL: "true", NOTE_LABEL: "true"})

    def test_full_worker_starts_another_and_notes_stay_where_they_are(self):
        self.cell("a")
        self.cell("b")
        self.cell("c")
        self.assertEqual(self.kube.created, [W0, W1])
        self.assertEqual(self.ran_in(), W1)
        # W1 has more free memory, but a's session is in W0.
        self.cell("a")
        self.assertEqual(self.ran_in(), W0)

    def test_new_note_goes_to_the_worker_with_the_most_free_memory(self):
        for note in "abc":
            self.cell(note)
        # W0 stopped b's session to make room; c's session in W1 has grown.
        del self.reported[W0]["b"]
        self.reported[W1]["c"] = 2 * GiB
        self.cell("d")
        self.assertEqual(self.ran_in(), W0)
        self.cell("e")
        self.assertEqual(self.ran_in(), W1)

    def test_at_the_most_workers_the_least_used_is_shared(self):
        for note in "abcd":
            self.cell(note)
        self.reported[W0] = {"a": 2 * GiB, "b": 2 * GiB}
        self.cell("e")
        self.assertEqual(self.ran_in(), W1)
        self.assertEqual(self.kube.created, [W0, W1])

    def test_idle_worker_is_stopped_but_warm_ones_are_kept(self):
        workers = self.make(min_workers=1)
        workers.adopt_running()
        self.assertEqual(self.kube.created, [W0])
        for note in "abc":
            self.cell(note, workers)
        self.assertEqual(self.kube.created, [W0, W1])
        self.reported = {}
        self.assertEqual(workers.reap_idle(), 0)
        self.now += 1201
        self.assertEqual(workers.reap_idle(), 1)
        self.assertEqual(self.kube.deleted, [W1])
        self.assertEqual(set(workers.workers), {W0})

    def test_warm_worker_gone_is_started_again(self):
        workers = self.make(min_workers=1)
        workers.adopt_running()
        del self.kube.pods[W0]
        workers.reap_idle()
        self.assertEqual(self.kube.created, [W0, W0])

    def test_worker_hosting_a_note_is_not_stopped(self):
        self.cell("a")
        self.now += 5000
        self.workers.reap_idle()
        self.now += 5000
        self.assertEqual(self.workers.reap_idle(), 0)
        self.assertEqual(self.kube.deleted, [])

    def test_restarted_gateway_finds_each_note_in_its_worker(self):
        for note in "abc":
            self.cell(note)
        fresh = self.make()
        fresh.adopt_running()
        self.assertEqual(fresh.placed, {"a": W0, "b": W0, "c": W1})
        self.cell("c", fresh)
        self.cell("d", fresh)
        self.assertEqual([ip for ip, _ in self.forwarded[-2:]], [W1, W1])
        self.assertEqual(self.kube.created, [W0, W1])

    def test_heavy_note_keeps_its_session_then_gets_a_pod_of_its_own(self):
        self.cell("a")
        self.reported[W0]["a"] = int(3.5 * GiB)
        self.workers.reap_idle()
        self.assertIn("a", self.workers.heavy)
        self.cell("a")
        self.assertEqual(self.ran_in(), W0)
        # The worker stopped the session (idle, or to make room).
        del self.reported[W0]["a"]
        self.cell("a")
        self.assertEqual(self.ran_in(), pod_name("a"))
        self.assertEqual(self.kube.created, [W0, pod_name("a")])

    def test_note_stopped_for_its_memory_gets_a_pod_of_its_own(self):
        self.cell("a")

        def out_of_memory(ip, payload, timeout, secret):
            del self.reported[W0]["a"]
            return {"success": False, "out_of_memory": True}

        self.workers.forward = out_of_memory
        self.assertTrue(self.cell("a")["out_of_memory"])
        self.workers.forward = self.forward
        self.cell("a")
        self.assertEqual(self.ran_in(), pod_name("a"))

    def test_restarted_gateway_knows_notes_in_pods_of_their_own_are_heavy(self):
        self.workers.heavy.add("a")
        self.cell("a")
        fresh = self.make()
        fresh.adopt_running()
        self.cell("a", fresh)
        self.assertEqual(self.ran_in(), pod_name("a"))

    def test_worker_lost_mid_cell_is_reported(self):
        self.cell("a")

        def dropped(ip, payload, timeout, secret):
            raise ConnectionResetError("connection reset by peer")

        self.workers.forward = dropped
        with self.assertLogs("note_workers", level="WARNING"):
            result = self.cell("a")
        self.assertIn("stopped while running this cell", result["markdown"])
        self.assertEqual(self.workers.busy, {})

    def test_full_quota_reports_busy_and_leaves_no_placement(self):
        self.kube.quota_full_until = self.now + 10_000
        result = self.cell("a")
        self.assertIn("quota", result["markdown"])
        self.workers.reap_idle()
        self.assertEqual(self.workers.placed, {})

    def test_note_is_served_while_its_worker_exists(self):
        self.assertFalse(self.workers.serving("a"))
        self.cell("a")
        self.assertTrue(self.workers.serving("a"))
        self.kube.save_seconds = 40
        self.kube.delete(W0)
        self.assertFalse(self.workers.serving("a"))


class FakeProc:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class SessionMemoryTests(unittest.TestCase):
    """The worker side, with fake R processes: nothing is started or signalled."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-workers-test-")
        self.addCleanup(self.temp.cleanup)
        self.handles = {}
        for note, pid in (("a", 101), ("b", 201)):
            project = os.path.join(self.temp.name, note)
            (Path(project) / note_kernel.KERNEL_DIR_REL).mkdir(parents=True)
            self.handles[note] = note_kernel.KernelHandle(project, pid, FakeProc())
        patcher = patch.dict(note_kernel._kernels, {h.project_dir: h for h in self.handles.values()}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # a's R (101) started a child (102); b's R is 201.
        self.table = {1: (0, 50 << 20), 101: (1, GiB), 102: (101, int(3.5 * GiB)), 201: (1, 2 * GiB)}

    def test_report_gives_each_note_its_whole_process_tree(self):
        report = session_memory.report(self.temp.name, read=lambda: self.table)
        self.assertEqual(sorted((r["thread_id"], r["rss_bytes"]) for r in report),
                         [("a", int(4.5 * GiB)), ("b", 2 * GiB)])

    def test_watchdog_stops_only_the_session_over_its_limit(self):
        with patch.object(note_kernel, "kill_kernel") as kill:
            stopped = session_memory.stop_over_limit("4Gi", read=lambda: self.table)
        self.assertEqual(stopped, [self.handles["a"]])
        kill.assert_called_once_with(self.handles["a"])
        self.assertEqual(self.handles["a"].out_of_memory, "4Gi")
        self.assertIsNone(self.handles["b"].out_of_memory)

    def test_cell_of_a_stopped_session_says_it_ran_out_of_memory(self):
        handle = self.handles["a"]
        handle.out_of_memory = "4Gi"
        handle.proc.returncode = -15
        with self.assertRaises(note_kernel.KernelOutOfMemory) as stopped:
            note_kernel.request_cell(handle, request_id="x", run_dir_rel="runs/x", source="1")
        self.assertIn("ran out of memory (limit 4Gi)", str(stopped.exception))
        self.assertNotIn(handle.project_dir, note_kernel._kernels)

    def test_session_that_died_otherwise_is_not_called_out_of_memory(self):
        handle = self.handles["b"]
        handle.proc.returncode = 1
        with self.assertRaises(note_kernel.KernelDied):
            note_kernel.request_cell(handle, request_id="x", run_dir_rel="runs/x", source="1")

    def test_out_of_memory_reaches_the_note_and_the_gateway(self):
        message = note_kernel.OUT_OF_MEMORY_MESSAGE.format(limit="4Gi")
        with patch.object(executor, "ensure_kernel", return_value=object()), \
                patch.object(executor, "request_cell", side_effect=note_kernel.KernelOutOfMemory(message)):
            result = executor.execute_note_cell(self.temp.name, "a", "x <- 1")
        self.assertFalse(result["success"])
        self.assertTrue(result["out_of_memory"])
        self.assertEqual(result["markdown"], f"**{message}**")

    def test_processes_are_read_from_proc(self):
        table = session_memory.processes()
        parent, rss = table[os.getpid()]
        self.assertEqual(parent, os.getppid())
        self.assertGreater(rss, 0)

    def test_memory_quantities(self):
        self.assertEqual(session_memory.parse_bytes("4Gi"), 4 * GiB)
        self.assertEqual(session_memory.parse_bytes("1.5Gi"), int(1.5 * GiB))
        self.assertEqual(session_memory.parse_bytes("500M"), 500_000_000)
        self.assertEqual(session_memory.parse_bytes("1024"), 1024)

    def test_sessions_route_needs_the_secret(self):
        client = TestClient(server.app)
        report = [{"thread_id": "a", "rss_bytes": GiB}]
        with patch.object(server, "INTERNAL_SECRET", "s"), patch.object(session_memory, "report", return_value=report):
            self.assertEqual(client.get("/api/sessions").status_code, 401)
            answer = client.get("/api/sessions", headers={"X-Internal-Secret": "s"})
        self.assertEqual(answer.json(), {"sessions": report})


class SaveAfterCellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-save-test-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / note_kernel.WORKSPACE_RELATIVE_PATH
        self.workspace.parent.mkdir(parents=True)
        patcher = patch.object(note_kernel, "SAVE_AFTER_CELL_MAX_BYTES", 100)
        patcher.start()
        self.addCleanup(patcher.stop)

    def asks(self):
        return note_kernel.save_after_cell(self.temp.name)

    def test_session_never_saved_is_asked_to_save_after_a_slow_cell(self):
        self.assertEqual(self.asks(), {"save_after_seconds": note_kernel.SAVE_AFTER_CELL_SECONDS})

    def test_small_saved_workspace_is_saved_again_and_a_large_one_is_not(self):
        self.workspace.write_bytes(b"x" * 100)
        self.assertEqual(self.asks(), {"save_after_seconds": note_kernel.SAVE_AFTER_CELL_SECONDS})
        self.workspace.write_bytes(b"x" * 101)
        self.assertEqual(self.asks(), {})

    def test_cell_request_carries_the_decision(self):
        handle = note_kernel.KernelHandle(self.temp.name, 101, FakeProc(returncode=0))
        with self.assertRaises(note_kernel.KernelDied):
            note_kernel.request_cell(handle, request_id="x", run_dir_rel="runs/x", source="1")
        request = json.loads((self.workspace.parent / note_kernel.REQUEST_FILE_NAME).read_text())
        self.assertEqual(request["save_after_seconds"], note_kernel.SAVE_AFTER_CELL_SECONDS)

    def test_kernel_saves_the_same_way_after_a_cell_and_on_shutdown(self):
        script = note_kernel.build_kernel_script()
        self.assertEqual(script.count(".note_save()"), 2)
        self.assertIn(f"<= {note_kernel.SAVE_AFTER_CELL_MAX_BYTES}", script)


class StalePidFileTests(unittest.TestCase):
    def test_process_counts_as_the_notes_only_when_it_works_in_the_note_folder(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertTrue(note_kernel._works_in(os.getpid(), os.getcwd()))
            self.assertFalse(note_kernel._works_in(os.getpid(), other))


if __name__ == "__main__":
    unittest.main()
