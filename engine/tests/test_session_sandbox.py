"""The R session sandbox, shown with Python child processes in place of R."""
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.kernel import sandbox

# A Python inside the read-only system paths, so it can run under the sandbox.
PYTHON = "/usr/bin/python3"

CHILD = r"""
import json, os, sys, tempfile
note, sibling, other_pid = sys.argv[1], sys.argv[2], int(sys.argv[3])
out = {"secret": os.environ.get("OMICSBASE_AUTH_SECRET"), "env": sorted(os.environ)}
def attempt(name, action):
    try:
        action()
        out[name] = "ok"
    except PermissionError:
        out[name] = "denied"
def write_note():
    with open("result.txt", "w") as f:
        f.write("x")
    os.rename("result.txt", "renamed.txt")
attempt("write_note", write_note)
attempt("write_tmp", lambda: tempfile.NamedTemporaryFile(dir=None).close())
attempt("list_sibling", lambda: os.listdir(sibling))
attempt("read_sibling", lambda: open(os.path.join(sibling, "secret.txt")).read())
attempt("write_sibling", lambda: open(os.path.join(sibling, "new.txt"), "w").close())
attempt("other_proc", lambda: open(f"/proc/{other_pid}/environ").read())
attempt("signal_other", lambda: os.kill(other_pid, 15))
attempt("meminfo", lambda: open("/proc/meminfo").read())
out["tmpdir"] = tempfile.gettempdir()
out["oom"] = open("/proc/self/oom_score_adj").read().strip()
out["pid"] = os.getpid()
print(json.dumps(out))
"""


@unittest.skipUnless(os.path.exists(PYTHON), f"needs {PYTHON}")
class SessionSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob3-sandbox-test-")
        projects = Path(self.temp.name) / "projects"
        self.note, self.sibling = projects / "note_a", projects / "note_b"
        self.note.mkdir(parents=True)
        self.sibling.mkdir()
        (self.sibling / "secret.txt").write_text("other note's data")
        # An unrelated process outside the session.
        self.other = subprocess.Popen(["/usr/bin/sleep", "60"])
        self.env = patch.dict(os.environ, {"OMICSBASE_AUTH_SECRET": "s3cret", "R_API_KEY": "k",
                                           "KUBERNETES_SERVICE_HOST": "10.0.0.1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.other.kill()
        self.other.wait()
        self.temp.cleanup()

    def run_child(self, mode):
        with patch.object(sandbox, "MODE", mode):
            argv, env = sandbox.session_command(
                str(self.note), [PYTHON, "-I", "-c", CHILD, str(self.note), str(self.sibling), str(self.other.pid)])
        proc = subprocess.Popen(argv, env=env, cwd=self.note, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stderr)
        out = json.loads(stdout)
        # The launcher execs the command, so the engine's pid is the command's.
        self.assertEqual(out["pid"], proc.pid)
        return out

    @unittest.skipUnless(sandbox.landlock_abi(), "kernel without Landlock")
    def test_session_is_confined_to_its_note(self):
        out = self.run_child("required")
        self.assertIsNone(out["secret"])
        self.assertFalse({"R_API_KEY", "KUBERNETES_SERVICE_HOST"} & set(out["env"]))
        self.assertEqual(out["write_note"], "ok")
        self.assertTrue((self.note / "renamed.txt").exists())
        self.assertEqual(out["write_tmp"], "ok")
        self.assertEqual(out["tmpdir"], str(self.note.resolve() / ".omicsbase" / "note-kernel" / "tmp"))
        self.assertEqual(out["list_sibling"], "denied")
        self.assertEqual(out["read_sibling"], "denied")
        self.assertEqual(out["write_sibling"], "denied")
        self.assertEqual(out["other_proc"], "denied")
        self.assertEqual(out["meminfo"], "ok")
        self.assertEqual(out["oom"], "1000")

    @unittest.skipUnless(sandbox.landlock_abi() >= 6, "Landlock without signal scoping")
    def test_session_cannot_signal_other_processes(self):
        out = self.run_child("required")
        self.assertEqual(out["signal_other"], "denied")
        self.assertIsNone(self.other.poll())

    def test_off_still_scrubs_the_environment(self):
        out = self.run_child("off")
        self.assertIsNone(out["secret"])
        self.assertFalse({"R_API_KEY", "KUBERNETES_SERVICE_HOST"} & set(out["env"]))
        self.assertEqual(out["list_sibling"], "ok")
        self.assertEqual(out["oom"], "1000")

    def test_required_fails_without_landlock(self):
        with patch.object(sandbox, "landlock_abi", return_value=0), patch.object(sandbox, "MODE", "required"):
            with self.assertRaisesRegex(sandbox.SandboxUnavailable, "no Landlock"):
                sandbox.session_command(str(self.note), ["Rscript"])

    def test_auto_warns_without_landlock(self):
        with patch.object(sandbox, "landlock_abi", return_value=0), patch.object(sandbox, "MODE", "auto"):
            with self.assertLogs("note_kernel", "WARNING"):
                argv, _ = sandbox.session_command(str(self.note), ["Rscript"])
        self.assertEqual(argv[-2:], ["plain", "Rscript"])

    def test_no_allowed_path_may_contain_the_note(self):
        with patch.dict(os.environ, {"R_LIBS_SITE": self.temp.name}):
            with self.assertRaisesRegex(sandbox.SandboxUnavailable, "would expose every note"):
                sandbox.allowed_paths(self.note.resolve(), sandbox.session_env(Path("/h"), Path("/t")))


class ReticulatePythonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.venv_python = Path(temp.name) / "python"
        self.venv_python.touch()

    def reticulate_python(self, value):
        with patch.object(sandbox, "_VENV_PYTHON", str(self.venv_python)), \
                patch.dict(os.environ, {"RETICULATE_PYTHON": value}):
            return sandbox.session_env(Path("/h"), Path("/t"))["RETICULATE_PYTHON"]

    def test_old_system_python_is_replaced_by_the_venv(self):
        self.assertEqual(self.reticulate_python("/usr/bin/python3"), str(self.venv_python))

    def test_other_values_are_left_alone(self):
        self.assertEqual(self.reticulate_python("/srv/py/bin/python"), "/srv/py/bin/python")


if __name__ == "__main__":
    unittest.main()
