"""Idle reaping, saving and restoring, the session limit and the output cap, with real R;
no model API calls."""
import asyncio
import glob
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from engine import server
from engine.kernel import executor, note_kernel


class KernelLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-limits-test-")
        self.patches = [patch.object(server, "PROJECTS_DIR", self.temp.name)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for path, handle in list(note_kernel._kernels.items()):
            if path.startswith(self.temp.name):
                note_kernel.kill_kernel(handle)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_cell(self, thread_id, code, timeout=30):
        return server._run_cell(code, thread_id, timeout)

    def scope(self, thread_id):
        return os.path.join(self.temp.name, thread_id)

    async def test_large_output_is_capped_on_disk_and_in_the_result(self):
        result = await asyncio.to_thread(self.run_cell, "big", 'for (i in 1:7) cat(strrep("x", 1e6), "\\n")')
        self.assertTrue(result["success"])
        self.assertLess(len(result["stdout"]), executor.OUTPUT_MAX_CHARS + 500)
        self.assertLess(len(result["markdown"]), executor.OUTPUT_MAX_CHARS + 1000)
        self.assertTrue("of output omitted" in result["stdout"])
        self.assertTrue("output truncated" in result["stdout"])
        console = os.path.join(self.scope("big"), result["run_dir"], note_kernel.CONSOLE_FILE_NAME)
        self.assertLess(os.path.getsize(console), note_kernel.CONSOLE_MAX_BYTES + 1000)
        events = os.path.join(self.scope("big"), result["run_dir"], note_kernel.EVENTS_FILE_NAME)
        self.assertLess(os.path.getsize(events), note_kernel.CONSOLE_MAX_BYTES * 1.2)
        # The session keeps working after a capped cell.
        result = await asyncio.to_thread(self.run_cell, "big", "print(40 + 2)")
        self.assertEqual(result["stdout"].strip(), "[1] 42")

    async def test_table_preview_reads_ten_rows_and_reports_the_full_size(self):
        result = await asyncio.to_thread(
            self.run_cell, "table", "print(data.frame(a = 1:50000, b = letters[1 + (0:49999) %% 26]))"
        )
        self.assertEqual(result["tables"][0]["rows"], 50000)
        self.assertEqual(result["tables"][0]["cols"], 2)
        self.assertEqual(result["tables"][0]["markdown"].count("\n"), 11)

    async def test_idle_session_is_saved_stopped_and_restored(self):
        await asyncio.to_thread(self.run_cell, "idle", "x <- 41; y <- letters")
        handle = note_kernel._kernels[self.scope("idle")]
        with patch.object(note_kernel, "IDLE_SECONDS", 1):
            time.sleep(1.5)
            self.assertEqual(await asyncio.to_thread(note_kernel.reap_idle_kernels), 1)
        self.assertNotIn(self.scope("idle"), note_kernel._kernels)
        self.assertFalse(note_kernel._alive(handle))
        result = await asyncio.to_thread(self.run_cell, "idle", "print(x + 1)")
        self.assertIn("session was restarted; 2 saved objects were restored", result["stdout"])
        self.assertIn("[1] 42", result["stdout"])
        # The notice appears once.
        result = await asyncio.to_thread(self.run_cell, "idle", "print(length(y))")
        self.assertEqual(result["stdout"].strip(), "[1] 26")

    async def test_restore_brings_back_bioconductor_objects_and_packages(self):
        result = await asyncio.to_thread(
            self.run_cell, "bioc",
            'if (requireNamespace("S4Vectors", quietly = TRUE)) {\n'
            '  library(S4Vectors); d <- DataFrame(a = 1:3); cat("loaded")\n'
            '}',
        )
        if "loaded" not in result["stdout"]:
            self.skipTest("S4Vectors is not installed here")
        with patch.object(note_kernel, "IDLE_SECONDS", 0):
            self.assertEqual(await asyncio.to_thread(note_kernel.reap_idle_kernels), 1)
        result = await asyncio.to_thread(
            self.run_cell, "bioc", 'print(class(d)); print("package:S4Vectors" %in% search())'
        )
        self.assertIn('"DFrame"', result["stdout"])
        self.assertIn("[1] TRUE", result["stdout"])

    async def test_engine_shutdown_saves_every_session(self):
        await asyncio.to_thread(self.run_cell, "one", "a <- 1")
        await asyncio.to_thread(self.run_cell, "two", "b <- 2")
        self.assertEqual(await asyncio.to_thread(note_kernel.save_all_kernels), 2)
        self.assertNotIn(self.scope("one"), note_kernel._kernels)
        result = await asyncio.to_thread(self.run_cell, "one", "print(a)")
        self.assertIn("restored", result["stdout"])
        self.assertIn("[1] 1", result["stdout"])
        result = await asyncio.to_thread(self.run_cell, "two", "print(b)")
        self.assertIn("[1] 2", result["stdout"])

    def workspace(self, thread_id):
        return os.path.join(self.scope(thread_id), note_kernel.WORKSPACE_RELATIVE_PATH)

    def reap_all(self):
        with patch.object(note_kernel, "IDLE_SECONDS", 0):
            return note_kernel.reap_idle_kernels()

    async def test_cells_cannot_reach_the_kernel_state(self):
        result = await asyncio.to_thread(self.run_cell, "state", "print(ls(all.names = TRUE))")
        self.assertEqual(result["stdout"].strip(), "character(0)")
        # Names the kernel once used, a cleared workspace and a new working directory.
        result = await asyncio.to_thread(
            self.run_cell, "state",
            'ws <- "saved"; objects_file <- "elsewhere.txt"; defaults <- NULL\n'
            "rm(list = setdiff(ls(all.names = TRUE), c('ws', 'objects_file', 'defaults')))\n"
            "setwd(tempdir()); x <- 41",
        )
        self.assertTrue(result["success"], result)
        self.assertIn("working directory was set back", result["stdout"])
        result = await asyncio.to_thread(self.run_cell, "state", "print(x + 1); print(basename(getwd()))")
        self.assertEqual(result["stdout"].strip(), '[1] 42\n[1] "state"')
        self.assertEqual(await asyncio.to_thread(self.reap_all), 1)
        self.assertFalse(os.path.exists(os.path.join(self.scope("state"), "saved")))
        result = await asyncio.to_thread(self.run_cell, "state", "print(c(x, nchar(ws))); print(ls())")
        self.assertIn("4 saved objects were restored", result["stdout"])
        self.assertIn("[1] 41  5", result["stdout"])
        self.assertIn('"defaults"     "objects_file" "ws"           "x"', result["stdout"])

    async def test_cleared_workspace_is_still_saved_and_restored(self):
        result = await asyncio.to_thread(self.run_cell, "cleared", "a <- 1; rm(list = ls()); b <- 2")
        self.assertTrue(result["success"], result)
        self.assertEqual(await asyncio.to_thread(self.reap_all), 1)
        result = await asyncio.to_thread(self.run_cell, "cleared", "print(ls())")
        self.assertIn("1 saved objects were restored", result["stdout"])
        self.assertIn('[1] "b"', result["stdout"])

    async def test_unreadable_workspace_is_moved_aside_once(self):
        workspace = self.workspace("bad")
        os.makedirs(os.path.dirname(workspace))
        with open(workspace, "wb") as f:
            f.write(b"saved\n")
        result = await asyncio.to_thread(self.run_cell, "bad", "y <- 1")
        self.assertIn("saved objects could not be restored", result["stdout"])
        self.assertIn("kept as workspace.RData.bad-", result["stdout"])
        moved = glob.glob(workspace + note_kernel.WORKSPACE_BAD_SUFFIX + "*")
        self.assertEqual(len(moved), 1)
        with open(moved[0], "rb") as f:
            self.assertEqual(f.read(), b"saved\n")
        # The session saves normally and the next one restores without the error.
        self.assertEqual(await asyncio.to_thread(self.reap_all), 1)
        result = await asyncio.to_thread(self.run_cell, "bad", "print(y)")
        self.assertIn("1 saved objects were restored", result["stdout"])
        self.assertNotIn("could not be restored", result["stdout"])
        self.assertIn("[1] 1", result["stdout"])

    async def test_workspace_saved_by_the_old_kernel_restores_only_user_objects(self):
        defaults = ", ".join(f'"{p}"' for p in note_kernel._R_DEFAULT_ATTACHED)
        os.makedirs(os.path.dirname(self.workspace("legacy")))
        subprocess.run(
            ["Rscript", "--vanilla", "-e",
             f'ws <- "{note_kernel.WORKSPACE_RELATIVE_PATH.as_posix()}"; '
             f'objects_file <- "{note_kernel.WORKSPACE_OBJECTS_RELATIVE_PATH.as_posix()}"; '
             f"defaults <- c({defaults}); .note_state <- new.env(); .note_run <- function() NULL; "
             ".disarm_fn <- function() NULL; x <- 1; save.image(ws)"],
            cwd=self.scope("legacy"), check=True,
        )
        result = await asyncio.to_thread(self.run_cell, "legacy", "print(ls(all.names = TRUE))")
        self.assertIn("1 saved objects were restored", result["stdout"])
        self.assertIn('[1] "x"', result["stdout"])

    async def test_running_session_is_not_reaped(self):
        running = asyncio.create_task(asyncio.to_thread(self.run_cell, "busy", "Sys.sleep(4); print('done')"))
        await asyncio.sleep(2)
        with patch.object(note_kernel, "IDLE_SECONDS", 0):
            self.assertEqual(await asyncio.to_thread(note_kernel.reap_idle_kernels), 0)
        result = await running
        self.assertIn("done", result["stdout"])

    async def test_session_limit_stops_least_recently_used_then_queues(self):
        with patch.object(note_kernel, "MAX_SESSIONS", 2), patch.object(note_kernel, "QUEUE_SECONDS", 2):
            await asyncio.to_thread(self.run_cell, "a", "a <- 1")
            await asyncio.to_thread(self.run_cell, "b", "b <- 2")
            await asyncio.to_thread(self.run_cell, "c", "c <- 3")
            self.assertNotIn(self.scope("a"), note_kernel._kernels)
            self.assertIn(self.scope("b"), note_kernel._kernels)
            self.assertIn(self.scope("c"), note_kernel._kernels)
            # Session a was saved when it was stopped.
            result = await asyncio.to_thread(self.run_cell, "a", "print(a)")
            self.assertIn("[1] 1", result["stdout"])
            # With both sessions busy, a third note waits, then gives up with a clear message.
            busy = [
                asyncio.create_task(asyncio.to_thread(self.run_cell, n, "Sys.sleep(6)"))
                for n in ("a", "c")
            ]
            await asyncio.sleep(1)
            start = time.monotonic()
            result = await asyncio.to_thread(self.run_cell, "d", "print('ran')")
            self.assertGreaterEqual(time.monotonic() - start, 2)
            self.assertFalse(result["success"])
            self.assertIn("R sessions are busy", result["error"])
            await asyncio.gather(*busy)


if __name__ == "__main__":
    unittest.main()
