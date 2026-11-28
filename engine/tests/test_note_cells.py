"""How a cell runs: it stops at its first error, and package installers neither install nor
refuse a whole cell. With real R; no model API calls."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import server
from engine.kernel import note_kernel

MISSING = "omicsbaseNoSuchPackage"
RUN = "c" * 24


class NoteCellTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-cells-test-")
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

    def run_cell(self, code, thread_id="note", execution_id=None):
        return server._run_cell(code, thread_id, 60, execution_id)

    async def require(self, package):
        result = await asyncio.to_thread(
            self.run_cell, f'cat(requireNamespace("{package}", quietly = TRUE))'
        )
        if result["stdout"].strip() != "TRUE":
            self.skipTest(f"{package} is not installed here")

    async def test_cell_stops_at_its_first_error_and_keeps_the_output_before_it(self):
        result = await asyncio.to_thread(
            self.run_cell, 'print("before")\nstop("boom")\nafter <- 1\nprint("after")', "note", RUN
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "boom")
        self.assertIn('[1] "before"\nError: boom', result["stdout"])
        self.assertNotIn("after", result["stdout"])
        status = server._execution_status("note", RUN, 0)
        self.assertEqual(status["phase"], "done")
        self.assertTrue(status["done"])
        self.assertFalse(status["result"]["success"])
        result = await asyncio.to_thread(self.run_cell, 'print(exists("after"))')
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"].strip(), "[1] FALSE")

    async def test_text_printed_in_a_loop_shows_while_the_cell_runs(self):
        code = ('print.step <- function(x, ...) cat("step method\\n")\n'
                'for (i in 1:2) {\n  print(i)\n  cat("cat", i, "\\n")\n  writeLines("line")\n'
                '  print(structure(i, class = "step"))\n  Sys.sleep(5)\n}\nx <- print(3)\nx')
        run = asyncio.create_task(asyncio.to_thread(self.run_cell, code, "note", RUN))
        output = ""
        for _ in range(300):
            await asyncio.sleep(0.1)
            status = server._execution_status("note", RUN, 0)
            if status and "step method" in status["output"]:
                output = status["output"]
                break
        # The loop's first step is shown before the loop, and so the expression, has ended.
        self.assertFalse(status["done"])
        self.assertEqual(output, "[1] 1\ncat 1 \nline\nstep method\n")
        result = await run
        self.assertTrue(result["success"], result)
        self.assertEqual(result["stdout"], "[1] 1\ncat 1 \nline\nstep method\n"
                         "[1] 2\ncat 2 \nline\nstep method\n[1] 3\n[1] 3\n")
        calls = json.loads((Path(self.temp.name) / "note" / result["run_dir"] / "calls.json").read_text())
        packages = {c["name"]: c.get("package") for c in calls["calls"]}
        self.assertEqual([packages[n] for n in ("print", "cat", "writeLines")], ["base"] * 3)

    async def test_install_guard_for_an_installed_package_runs(self):
        await self.require("BiocManager")
        result = await asyncio.to_thread(
            self.run_cell,
            'if (!requireNamespace("stats", quietly = TRUE)) BiocManager::install("stats")\n'
            'install.packages("stats")\nprint("ran")',
        )
        self.assertTrue(result["success"], result)
        self.assertIn("install.packages: 'stats' is already installed in OmicsBase; nothing was installed.",
                      result["stdout"])
        self.assertIn('[1] "ran"', result["stdout"])

    async def test_installers_name_a_missing_package(self):
        refused = ("is not installed in OmicsBase, and packages cannot be installed from a note. "
                   "Use an installed package instead.")
        calls = {
            f'install.packages("{MISSING}")': "install.packages",
            f'utils::install.packages(c("stats", "{MISSING}"))': "install.packages",
            f'BiocManager::install("{MISSING}")': "BiocManager::install",
            f'remotes::install_github("someone/{MISSING}@main")': "remotes::install_github",
            f'devtools::install_github("someone/{MISSING}")': "devtools::install_github",
            f'pak::pkg_install("bioc::{MISSING}")': "pak::pkg_install",
            f'pak::pak("{MISSING}")': "pak::pak",
            f"pacman::p_install({MISSING})": "pacman::p_install",
        }
        for code, installer in calls.items():
            package = installer.split("::")[0] if "::" in installer else "utils"
            with self.subTest(code=code):
                await self.require(package)
                result = await asyncio.to_thread(self.run_cell, f'{code}\nprint("not reached")')
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], f"{installer}: '{MISSING}' {refused}")
                self.assertNotIn("not reached", result["stdout"])

    async def test_installer_without_packages_is_refused(self):
        await self.require("BiocManager")
        result = await asyncio.to_thread(self.run_cell, "BiocManager::install()")
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"],
            "BiocManager::install: packages cannot be installed or updated from a note in OmicsBase.",
        )

    async def test_installers_stay_disarmed_once_their_package_is_attached(self):
        await self.require("BiocManager")
        result = await asyncio.to_thread(self.run_cell, f'library(BiocManager)\ninstall("{MISSING}")')
        self.assertFalse(result["success"])
        self.assertTrue(result["error"].startswith(f"BiocManager::install: '{MISSING}' is not installed"))

    async def test_pacman_loads_installed_packages_and_refuses_missing_ones(self):
        await self.require("pacman")
        result = await asyncio.to_thread(
            self.run_cell, f'pacman::p_load(tools, {MISSING})\nprint("not reached")'
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["error"].startswith(f"pacman::p_load: '{MISSING}' is not installed"))
        result = await asyncio.to_thread(self.run_cell, 'print("package:tools" %in% search())')
        self.assertEqual(result["stdout"].strip(), "[1] TRUE")


if __name__ == "__main__":
    unittest.main()
