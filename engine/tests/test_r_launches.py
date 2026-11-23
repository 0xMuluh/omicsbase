"""r_help and Quarto renders start their processes sandboxed. At the subprocess call the
command after the launcher is swapped for a Python child, so the engine's own launcher
and env are used but no R or Quarto runs."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from engine import server
from engine.kernel import sandbox

SECRET = "test-secret"
PYTHON = "/usr/bin/python3"

# Reports what the launched process could do, on stdout and in a file in its folder.
CHILD = r"""
import json, os, sys
out = {"secret": os.environ.get("OMICSBASE_AUTH_SECRET"), "cwd": os.getcwd()}
try:
    os.listdir(sys.argv[1])
    out["list_other_note"] = "ok"
except PermissionError:
    out["list_other_note"] = "denied"
with open("child.json", "w") as f:
    json.dump(out, f)
print(json.dumps(out))
"""


@unittest.skipUnless(os.path.exists(PYTHON) and sandbox.landlock_abi(), "needs /usr/bin/python3 and Landlock")
class LaunchSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-launch-test-")
        self.root = Path(self.temp.name)
        self.other = self.root / "other_note"
        self.other.mkdir()
        (self.root / "note_a").mkdir()
        (self.root / "note_a" / "index.qmd").write_text("# a\n")
        self.commands = []
        real_run, real_exec = subprocess.run, asyncio.create_subprocess_exec

        def swap(args):
            # The launcher is [python, -I, sandbox.py, folder, mode]; the command follows.
            self.assertEqual(args[4], "landlock")
            self.commands.append(list(args[5:]))
            return list(args[:5]) + [PYTHON, "-I", "-c", CHILD, str(self.other)]

        def run(args, **kwargs):
            return real_run(swap(args), **kwargs)

        async def create_subprocess_exec(*args, **kwargs):
            return await real_exec(*swap(args), **kwargs)

        self.patches = [
            patch.object(server, "PROJECTS_DIR", self.temp.name),
            patch.object(server, "INTERNAL_SECRET", SECRET),
            patch.dict(os.environ, {"OMICSBASE_AUTH_SECRET": SECRET}),
            patch.object(sandbox, "MODE", "required"),
            patch.object(subprocess, "run", run),
            patch.object(asyncio, "create_subprocess_exec", create_subprocess_exec),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_r_help_runs_in_a_throwaway_folder(self):
        out = json.loads(server._run_r_helper_script("cat(1)"))
        self.assertEqual(self.commands, [["Rscript", "--vanilla", "-e", "cat(1)"]])
        self.assertIsNone(out["secret"])
        self.assertEqual(out["list_other_note"], "denied")
        self.assertFalse(Path(out["cwd"]).is_relative_to(self.temp.name))
        self.assertFalse(os.path.exists(out["cwd"]))

    def test_quarto_tool_is_confined_to_its_note(self):
        server.render_quarto_report("note_a")
        self.assertEqual(self.commands[0][:2], ["quarto", "render"])
        out = json.loads((self.root / "note_a" / "child.json").read_text())
        self.assertIsNone(out["secret"])
        self.assertEqual(out["list_other_note"], "denied")

    def test_quarto_endpoint_is_confined_to_its_note(self):
        response = TestClient(server.app).post("/api/projects/note_a/render",
                                               headers={"X-Internal-Secret": SECRET})
        self.assertEqual(self.commands[0][:2], ["quarto", "render"])
        self.assertEqual(response.json()["exit_code"], 0)
        out = json.loads(response.json()["stdout"])
        self.assertIsNone(out["secret"])
        self.assertEqual(out["list_other_note"], "denied")
        self.assertTrue((self.root / "note_a" / "child.json").exists())


if __name__ == "__main__":
    unittest.main()
