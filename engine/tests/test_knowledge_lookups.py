"""Book searches made for a note are remembered in its look-up log."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import server


class ServerLookupTests(unittest.TestCase):
    RESULT = {"status": "success", "count": 1, "markdown": "found", "matches": [
        {"book": "oma", "heading": "Alpha diversity", "source_file": "inst/pages/alpha_diversity.qmd",
         "anchor": "sec-alpha-diversity"}]}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-lookups-test-")
        self.patches = [
            patch.object(server, "PROJECTS_DIR", self.temp.name),
            patch.object(server, "INTERNAL_SECRET", "test-secret"),
            patch.object(server, "search_bioc_knowledge", return_value=self.RESULT),
        ]
        for item in self.patches:
            item.start()
        self.log = Path(self.temp.name) / "note-1" / ".omicsbase" / "knowledge-lookups.jsonl"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_tool_logs_for_its_note_only(self):
        self.assertEqual(server.search_bioc_books("alpha diversity"), "found")
        self.assertEqual(os.listdir(self.temp.name), [])
        server.search_bioc_books("alpha diversity", thread_id="../outside")
        self.assertEqual(os.listdir(self.temp.name), [])
        server.search_bioc_books("alpha diversity", thread_id="note-1")
        self.assertEqual(json.loads(self.log.read_text())["anchor"], "sec-alpha-diversity")

    def test_http_search_logs_for_internal_callers_only(self):
        client = TestClient(server.app)
        response = client.get("/api/knowledge/search", params={"q": "alpha", "thread_id": "note-1"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.log.exists())
        client.get("/api/knowledge/search", params={"q": "alpha", "thread_id": "note-1"},
                   headers={"X-Internal-Secret": "test-secret"})
        self.assertEqual(len(self.log.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
