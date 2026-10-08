"""Uploads into a note and note copies go through the engine's API, not a shared disk."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import server

SECRET = "test-secret"


class NoteFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-note-files-test-")
        self.patches = [
            patch.object(server, "PROJECTS_DIR", self.temp.name),
            patch.object(server, "INTERNAL_SECRET", SECRET),
            patch.dict(os.environ, {"OMICSBASE_AUTH_SECRET": SECRET}),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app)
        self.root = Path(self.temp.name)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def put(self, note, name, body, secret=SECRET):
        return self.client.put(f"/api/projects/{note}/data/{name}", content=body,
                               headers={"X-Internal-Secret": secret})

    def test_upload_lands_in_the_note_data_folder(self):
        response = self.put("note1", "counts.tsv", b"a\tb\n1\t2\n")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "workspacePath": "data/counts.tsv", "bytes": 8})
        self.assertEqual((self.root / "note1" / "data" / "counts.tsv").read_bytes(), b"a\tb\n1\t2\n")
        self.assertEqual([p.name for p in (self.root / "note1" / "data").iterdir()], ["counts.tsv"])

    def test_upload_keeps_the_existing_working_copy(self):
        self.put("note1", "counts.tsv", b"original")
        response = self.put("note1", "counts.tsv", b"replacement")
        self.assertEqual(response.json()["bytes"], len(b"original"))
        self.assertEqual((self.root / "note1" / "data" / "counts.tsv").read_bytes(), b"original")

    def test_head_tells_whether_a_note_already_has_the_file(self):
        url = "/api/projects/note1/data/counts.tsv"
        headers = {"X-Internal-Secret": SECRET}
        self.assertEqual(self.client.head(url, headers=headers).status_code, 404)
        self.put("note1", "counts.tsv", b"x")
        self.assertEqual(self.client.head(url, headers=headers).status_code, 200)

    def test_upload_needs_the_secret(self):
        self.assertEqual(self.put("note1", "counts.tsv", b"x", secret="wrong").status_code, 401)
        self.assertFalse((self.root / "note1").exists())

    def test_upload_rejects_unsafe_names(self):
        for note, name in (("note1", ".hidden"), ("note1", "..%2Fescape"), ("bad.id", "x.tsv")):
            self.assertIn(self.put(note, name, b"x").status_code, (400, 404))
        self.assertFalse(any(self.root.rglob("escape")))

    def test_copy_takes_files_and_saved_session_but_not_the_live_process(self):
        kernel = self.root / "src" / ".omicsbase" / "note-kernel"
        kernel.mkdir(parents=True)
        (kernel / "workspace.RData").write_bytes(b"saved")
        (kernel / "kernel.pid").write_text("12345")
        (kernel / "execute.lock").write_text("")
        (self.root / "src" / "data").mkdir()
        (self.root / "src" / "data" / "counts.tsv").write_text("x")
        response = self.client.post("/api/projects/dst/copy", json={"source": "src"},
                                    headers={"X-Internal-Secret": SECRET})
        self.assertEqual(response.json(), {"success": True, "copied": True})
        copied = self.root / "dst" / ".omicsbase" / "note-kernel"
        self.assertEqual((copied / "workspace.RData").read_bytes(), b"saved")
        self.assertFalse((copied / "kernel.pid").exists())
        self.assertFalse((copied / "execute.lock").exists())
        self.assertTrue((self.root / "dst" / "data" / "counts.tsv").exists())

    def test_copy_of_a_note_without_files_is_a_no_op(self):
        response = self.client.post("/api/projects/dst/copy", json={"source": "missing"},
                                    headers={"X-Internal-Secret": SECRET})
        self.assertEqual(response.json(), {"success": True, "copied": False})
        self.assertFalse((self.root / "dst").exists())

    def test_copy_needs_the_secret_and_safe_ids(self):
        self.assertEqual(self.client.post("/api/projects/dst/copy", json={"source": "src"}).status_code, 401)
        bad = self.client.post("/api/projects/dst/copy", json={"source": "../etc"},
                               headers={"X-Internal-Secret": SECRET})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
