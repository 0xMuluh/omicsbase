"""The files a cell saved in its note's folder, reported for download under the cell."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.kernel import executor
from engine.kernel.cell_files import cell_files as list_cell_files, snapshot

BASE = "http://engine:8001"


class CellFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-cell-files-test-")
        self.note = Path(self.temp.name) / "note-1"
        self.note.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rel, body=b"x"):
        path = self.note / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def files(self, before, **kwargs):
        return list_cell_files(str(self.note), before, "note-1", BASE, **kwargs)

    def test_new_and_changed_files_are_listed_with_sizes_and_urls(self):
        self.write("data/counts.tsv", b"a\tb\n")
        self.write("results/old.csv", b"1,2\n")
        self.write("kept.rds", b"same")
        before = snapshot(str(self.note))
        self.write("results/old.csv", b"1,2\n3,4\n")
        self.write("figures/volcano plot.pdf", b"%PDF-1.4")
        self.write("alpha.csv", b"")
        self.assertEqual(self.files(before), {"files": [
            {"path": "alpha.csv", "bytes": 0, "url": f"{BASE}/projects/note-1/alpha.csv"},
            {"path": "figures/volcano plot.pdf", "bytes": 8,
             "url": f"{BASE}/projects/note-1/figures/volcano%20plot.pdf"},
            {"path": "results/old.csv", "bytes": 8, "url": f"{BASE}/projects/note-1/results/old.csv"},
        ]})

    def test_a_file_rewritten_with_the_same_size_counts_by_its_time(self):
        path = self.write("table.csv", b"1,2\n")
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        before = snapshot(str(self.note))
        self.write("table.csv", b"3,4\n")
        self.assertEqual([f["path"] for f in self.files(before)["files"]], ["table.csv"])

    def test_engine_bookkeeping_and_hidden_files_are_left_out(self):
        before = snapshot(str(self.note))
        for rel in ("runs/cell_1/plots/plot_001.png", "runs/cell_1/.note_console.txt",
                    ".omicsbase/note-kernel/kernel.pid", ".omicsbase/note-kernel/home/.Rhistory",
                    "_site/index.html", "_freeze/report/execute-results/html.json",
                    ".git/HEAD", ".Rhistory", "data/.upload-123", "results/.cache/x"):
            self.write(rel)
        os.symlink("/etc/hostname", self.note / "linked.txt")
        self.write("results/runs/summary.csv")  # runs/ is the engine's only at the top
        self.assertEqual([f["path"] for f in self.files(before)["files"]], ["results/runs/summary.csv"])

    def test_more_files_than_the_cap_are_truncated(self):
        before = snapshot(str(self.note))
        for i in range(5):
            self.write(f"out/file_{i}.txt")
        listed = self.files(before, max_files=3)
        self.assertEqual([f["path"] for f in listed["files"]], [f"out/file_{i}.txt" for i in range(3)])
        self.assertTrue(listed["files_truncated"])
        self.assertNotIn("files_truncated", self.files(before))

    def test_a_large_folder_is_listed_only_up_to_the_entry_cap(self):
        for i in range(20):
            self.write(f"data/sample_{i:02d}.fastq")
        before = snapshot(str(self.note), max_entries=10)
        self.assertFalse(before.complete)
        self.assertLessEqual(len(before.files), 10)
        self.write("result.csv")
        # Files the first listing did not reach count when modified since the cell started.
        self.assertIn("result.csv", [f["path"] for f in self.files(before)["files"]])
        for path in (self.note / "data").iterdir():
            if f"data/{path.name}" not in before.files:
                os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        self.assertEqual([f["path"] for f in self.files(before)["files"]], ["result.csv"])


class ExecuteResultTests(unittest.TestCase):
    def test_execute_result_lists_the_cell_files_but_not_its_plots(self):
        with tempfile.TemporaryDirectory() as projects:
            note = Path(projects) / "note-1"
            (note / "data").mkdir(parents=True)
            (note / "data" / "counts.tsv").write_text("a\n1\n")

            def fake_request(handle, request_id, run_dir_rel, source, timeout_seconds, cancel_check, on_phase=None):
                plots = note / run_dir_rel / "plots"
                plots.mkdir(parents=True)
                (plots / "plot_001.png").write_bytes(b"\x89PNG" + b"0" * 200)
                (note / "results").mkdir()
                (note / "results" / "alpha.csv").write_text("x\n1\n")
                return {"id": request_id}

            with patch.object(executor, "ensure_kernel", return_value=object()), \
                    patch.object(executor, "request_cell", side_effect=fake_request):
                result = executor.execute_note_cell(projects, "note-1", "write.csv(x, 'results/alpha.csv')",
                                                    base_url=BASE)
        self.assertEqual(len(result["plots"]), 1)
        self.assertEqual(result["files"], [
            {"path": "results/alpha.csv", "bytes": 4, "url": f"{BASE}/projects/note-1/results/alpha.csv"},
        ])
        self.assertNotIn("files_truncated", result)


if __name__ == "__main__":
    unittest.main()
