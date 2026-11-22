"""The status record of a cell run: phases, console text while running, restarts. No R."""
import fcntl
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import note_pods, server
from engine.kernel import cell_status, executor, note_kernel

SECRET = "test-secret"
RUN = "a" * 24
RUN_DIR = os.path.join("runs", "cell_test")


class ExecutionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-status-test-")
        self.release = threading.Event()
        self.patches = [
            patch.object(server, "PROJECTS_DIR", self.temp.name),
            patch.object(server, "INTERNAL_SECRET", SECRET),
            patch.object(server, "execute_note_cell", self.execute),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app, headers={"X-Internal-Secret": SECRET})
        self.note = Path(self.temp.name) / "note"
        self.kernel_dir = self.note / note_kernel.KERNEL_DIR_REL

    def tearDown(self):
        self.release.set()
        for thread in threading.enumerate():
            if thread.name.startswith("cell-"):
                thread.join(5)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def execute(self, projects_dir, thread_id, code, base_url, timeout_seconds, cancel_check, on_status):
        """Stands in for R: starts, prints, waits to be released, prints again."""
        console = Path(projects_dir) / thread_id / RUN_DIR / note_kernel.CONSOLE_FILE_NAME
        console.parent.mkdir(parents=True, exist_ok=True)
        on_status(run_dir=RUN_DIR, cell_id="test")
        on_status("starting_r")
        console.write_text("first\n", encoding="utf-8")
        on_status("running")
        self.release.wait(5)
        with console.open("a", encoding="utf-8") as f:
            f.write("second\n")
        return {"success": True, "stdout": "first\nsecond\n", "run_dir": RUN_DIR, "cancelled": cancel_check()}

    def post(self, **body):
        return self.client.post("/api/execute", json={"code": "1", "thread_id": "note", **body})

    def status(self, execution_id=RUN, offset=0, thread_id="note"):
        return self.client.get(f"/api/executions/{execution_id}", params={"thread_id": thread_id, "offset": offset})

    def wait_for(self, phase, execution_id=RUN):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            body = self.status(execution_id).json()
            if body["phase"] == phase:
                return body
            time.sleep(0.02)
        self.fail(f"run did not reach {phase}: {body}")

    def record(self, execution_id=RUN, **fields):
        self.kernel_dir.mkdir(parents=True, exist_ok=True)
        (self.kernel_dir / f"exec-{execution_id}.json").write_text(json.dumps(fields))

    def test_cell_started_without_waiting_is_followed_through_its_status(self):
        response = self.post(execution_id=RUN, wait=False)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"execution_id": RUN, "phase": "waiting_for_cell"})
        running = self.wait_for("running")
        self.assertFalse(running["done"])
        self.assertIsNone(running["result"])
        self.assertEqual(running["output"], "first\n")
        self.assertEqual(running["next_offset"], 6)
        self.assertEqual(running["started_at"], running["phase_started_at"])
        self.assertFalse(running["cancel_requested"])
        self.release.set()
        done = self.wait_for("done")
        self.assertTrue(done["done"])
        self.assertTrue(done["result"]["success"])
        self.assertEqual(done["started_at"], running["started_at"])
        self.assertEqual(self.status(offset=running["next_offset"]).json()["output"], "second\n")
        self.assertEqual(self.status(offset=13).json()["next_offset"], 13)

    def test_waiting_call_still_answers_with_the_result(self):
        self.release.set()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stdout"], "first\nsecond\n")
        self.assertEqual(list(self.kernel_dir.glob("exec-*")), [])
        response = self.post(execution_id=RUN)
        self.assertEqual(response.json()["stdout"], "first\nsecond\n")
        self.assertEqual(self.status().json()["phase"], "done")

    def test_not_waiting_needs_a_valid_execution_id(self):
        self.assertEqual(self.post(wait=False).status_code, 400)
        self.assertEqual(self.post(wait=False, execution_id="../x").status_code, 400)

    def test_unknown_run_is_not_found_and_the_secret_is_needed(self):
        self.assertEqual(self.status("b" * 24).status_code, 404)
        self.assertEqual(self.status("not-an-id").status_code, 400)
        self.record(phase="done", done=True)
        response = TestClient(server.app).get(f"/api/executions/{RUN}", params={"thread_id": "note"})
        self.assertEqual(response.status_code, 401)

    def test_cell_behind_another_waits_for_it_and_shows_a_cancel_request(self):
        self.kernel_dir.mkdir(parents=True)
        with (self.kernel_dir / "execute.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.post(execution_id=RUN, wait=False)
            time.sleep(0.2)
            waiting = self.status().json()
            self.assertEqual(waiting["phase"], "waiting_for_cell")
            self.assertIsNone(waiting["started_at"])
            self.client.post("/api/cancel", json={"thread_id": "note", "execution_id": RUN})
            self.assertTrue(self.status().json()["cancel_requested"])
        done = self.wait_for("done")
        self.assertTrue(done["result"]["cancelled"])
        self.assertFalse(done["cancel_requested"])

    def test_failure_outside_r_ends_the_run_with_an_error(self):
        def broken(**kwargs):
            raise RuntimeError("disk full")

        with patch.object(server, "execute_note_cell", broken), self.assertLogs("engine", level="ERROR"):
            self.post(execution_id=RUN, wait=False)
            done = self.wait_for("done")
        self.assertFalse(done["result"]["success"])
        self.assertIn("disk full", done["result"]["error"])

    def test_run_left_by_an_earlier_engine_is_reported_interrupted(self):
        self.record(phase="waiting_for_slot", phase_started_at=1.0, boot_id="earlier", done=False)
        body = self.status().json()
        self.assertEqual(body["phase"], "interrupted")
        self.assertTrue(body["done"])
        self.assertEqual(body["result"]["error"], "Interrupted by a server restart. Run the cell again.")
        self.assertTrue(cell_status.read(str(self.note), RUN)["done"])
        self.record("c" * 24, phase="running", boot_id="earlier", done=True, result={"success": True})
        self.assertEqual(self.status("c" * 24).json()["phase"], "running")

    def test_gateway_reports_a_running_cell_interrupted_only_when_its_pod_is_gone(self):
        self.record(phase="running", phase_started_at=1.0, started_at=1.0, boot_id="the-pod", done=False)
        with patch.object(note_pods, "ENABLED", True):
            with patch.object(note_pods, "serving", lambda thread_id: True):
                self.assertEqual(self.status().json()["phase"], "running")
            with patch.object(note_pods, "serving", lambda thread_id: False):
                self.assertEqual(self.status().json()["phase"], "interrupted")

    def test_gateway_records_its_phases_and_cancels_while_waiting_for_a_pod(self):
        seen = {}

        def run_cell(thread_id, payload, timeout_seconds, on_phase, cancelled):
            on_phase("waiting_for_slot")
            seen["timeout"] = timeout_seconds
            seen["phase"] = cell_status.read(str(self.note), RUN)["phase"]
            seen["cancelled"] = cancelled()
            self.client.post("/api/cancel", json={"thread_id": "note", "execution_id": RUN})
            return {"success": False, "cancelled": cancelled(), "error": "Cancelled"}

        with patch.object(note_pods, "ENABLED", True), patch.object(note_pods, "run_cell", run_cell):
            self.assertEqual(self.post(execution_id=RUN, wait=False).json()["phase"], "starting_pod")
            done = self.wait_for("done")
            self.assertEqual(seen, {"timeout": None, "phase": "waiting_for_slot", "cancelled": False})
            self.assertTrue(done["result"]["cancelled"])
            self.assertFalse(done["cancel_requested"])
            self.post(execution_id="d" * 24)
            self.assertEqual(seen["timeout"], server.CELL_TIMEOUT_SECONDS + note_kernel.R_START_SECONDS + 60)

    def test_status_files_older_than_a_day_go_when_a_cell_starts(self):
        self.record("e" * 24, phase="done", done=True)
        self.record("f" * 24, phase="done", done=True)
        old = time.time() - cell_status.KEEP_SECONDS - 60
        os.utime(self.kernel_dir / f"exec-{'e' * 24}.json", (old, old))
        self.release.set()
        self.post(execution_id=RUN)
        self.assertEqual(sorted(p.name for p in self.kernel_dir.glob("exec-*")),
                         [f"exec-{RUN}.json", f"exec-{'f' * 24}.json"])

    def test_status_files_are_not_copied_with_a_note(self):
        names = ["exec-" + RUN + ".json", "workspace.RData", "kernel.R"]
        self.assertEqual(server._SESSION_RUNTIME_FILES("dir", names), {names[0]})

    def test_output_is_read_in_chunks_that_end_on_whole_characters(self):
        console = self.note / RUN_DIR / note_kernel.CONSOLE_FILE_NAME
        console.parent.mkdir(parents=True)
        text = "ä" * cell_status.OUTPUT_CHUNK_BYTES
        console.write_text("x" + text, encoding="utf-8")
        record = {"run_dir": RUN_DIR}
        parts, offset = [], 0
        while True:
            part, next_offset = cell_status.read_output(str(self.note), record, offset)
            if next_offset == offset:
                break
            self.assertLessEqual(next_offset - offset, cell_status.OUTPUT_CHUNK_BYTES)
            parts.append(part)
            offset = next_offset
        self.assertEqual("".join(parts), "x" + text)
        self.assertEqual(len(parts), 3)
        self.assertEqual(cell_status.read_output(str(self.note), {}, 5), ("", 5))

    def test_runs_a_pod_has_not_finished_are_counted(self):
        self.record("1" * 24, phase="running", done=False)
        self.record("2" * 24, phase="waiting_for_cell", done=False)
        self.record("3" * 24, phase="starting_pod", done=False)
        self.record("4" * 24, phase="done", done=True)
        self.assertEqual(cell_status.active_runs(str(self.note)), 2)
        self.assertEqual(cell_status.active_runs(str(self.note.parent / "other")), 0)


class FakeR:
    def poll(self):
        return None


class KernelPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-phase-test-")
        self.root = Path(self.temp.name) / note_kernel.KERNEL_DIR_REL
        self.root.mkdir(parents=True)
        self.handle = note_kernel.KernelHandle(self.temp.name, 0, FakeR())
        self.phases = []
        self.patches = [patch.object(note_kernel, "kill_kernel", lambda handle: None),
                        patch.object(note_kernel, "POLL_INTERVAL", 0.01)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        note_kernel._kernels.pop(self.temp.name, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, timeout_seconds=1):
        return note_kernel.request_cell(self.handle, request_id="r1", run_dir_rel="run", source="1",
                                        timeout_seconds=timeout_seconds, on_phase=self.phases.append)

    def test_r_that_never_begins_the_cell_hits_the_start_limit_not_the_cell_limit(self):
        with patch.object(note_kernel, "R_START_SECONDS", 0.2):
            with self.assertRaisesRegex(note_kernel.KernelTimeout, "did not start"):
                self.request(timeout_seconds=600)
        self.assertEqual(self.phases, [])

    def test_cell_limit_counts_from_when_r_begins_the_cell(self):
        def r():
            time.sleep(0.4)
            (Path(self.temp.name) / "run").mkdir()
            (Path(self.temp.name) / "run" / note_kernel.CONSOLE_FILE_NAME).touch()
            time.sleep(0.4)
            (self.root / "done-r1.json").write_text('{"id": "r1", "status": "ok"}')

        threading.Thread(target=r).start()
        # The cell ends after the start limit has passed: that limit stopped counting
        # when R began the cell.
        with patch.object(note_kernel, "R_START_SECONDS", 0.6):
            self.assertEqual(self.request()["status"], "ok")
        self.assertEqual(self.phases, ["running"])

    def test_new_session_reports_starting_or_restoring(self):
        with patch.object(note_kernel, "start_kernel", lambda project_dir: self.handle):
            note_kernel.ensure_kernel(self.temp.name, on_phase=self.phases.append)
            note_kernel.ensure_kernel(self.temp.name, on_phase=self.phases.append)
            self.assertEqual(self.phases, ["starting_r"])
            note_kernel._kernels.pop(self.temp.name)
            (self.root / "workspace.RData").touch()
            note_kernel.ensure_kernel(self.temp.name, on_phase=self.phases.append)
        self.assertEqual(self.phases, ["starting_r", "restoring_session"])

    def test_waiting_for_a_session_is_not_taken_from_the_cell_limit(self):
        seen = {}

        def ensure(thread_dir, on_phase):
            return self.handle

        def request(handle, **kwargs):
            seen.update(kwargs)
            return {"status": "ok"}

        reports = []
        with patch.object(executor, "ensure_kernel", ensure), patch.object(executor, "request_cell", request):
            result = executor.execute_note_cell(
                self.temp.name, "note", "1", timeout_seconds=77,
                on_status=lambda phase=None, **fields: reports.append(phase or fields))
        self.assertEqual(seen["timeout_seconds"], 77)
        self.assertEqual(reports, [{"run_dir": result["run_dir"], "cell_id": result["cell_id"]}])

    def test_r_flushes_console_and_events_after_each_write(self):
        script = note_kernel.build_kernel_script()
        self.assertIn("flush(.note_state$con)", script)
        self.assertIn("flush(.note_state$ev_con)", script)


if __name__ == "__main__":
    unittest.main()
