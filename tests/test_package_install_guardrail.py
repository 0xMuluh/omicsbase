import unittest
from engine.kernel.executor import check_blocked_package_install, execute_note_cell
import tempfile
import os


class TestPackageInstallGuardrail(unittest.TestCase):
    def test_blocked_install_calls(self):
        blocked_snippets = [
            'install.packages("scrapper")',
            'install.packages(c("scrapper", "SingleR"))',
            'utils::install.packages("DESeq2")',
            'BiocManager::install("SingleR")',
            'BiocManager::install(c("xcms", "EBImage"))',
            'remotes::install_github("tidyomics/tidySpatialWorkshop")',
            'devtools::install_version("ggplot2", version = "3.4.0")',
            'pak::pkg_install("rlang")',
            'pak::pak("scrapper")',
            'pacman::p_load(scrapper)',
            'pacman::p_install(scrapper)',
            '  install.packages("foo")  # indented',
            'x <- 1\nBiocManager::install("bar")\ny <- 2',
        ]
        for snippet in blocked_snippets:
            matched = check_blocked_package_install(snippet)
            self.assertIsNotNone(
                matched,
                f"Snippet should be blocked: {snippet!r}"
            )

    def test_allowed_code(self):
        allowed_snippets = [
            'library(scrapper)',
            'require(SingleR)',
            'suppressPackageStartupMessages(library(EBImage))',
            '# install.packages("commented_out")',
            '# BiocManager::install("commented_out")',
            'df <- data.frame(note = "installed.packages() can be mentioned in strings")',
            'is_installed <- requireNamespace("scrapper", quietly = TRUE)',
        ]
        for snippet in allowed_snippets:
            matched = check_blocked_package_install(snippet)
            self.assertIsNone(
                matched,
                f"Snippet should be allowed: {snippet!r}, but matched: {matched}"
            )

    def test_executor_blocks_without_kernel_invocation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = execute_note_cell(
                projects_dir=tmp_dir,
                thread_id="test_thread",
                code='install.packages("scrapper")',
            )
            self.assertFalse(res["success"])
            self.assertIn("Package Installation Blocked", res["markdown"])
            self.assertIn("Dynamic package installation", res["error"])
            # Ensure no kernel was started (no kernel.pid created)
            kernel_pid_path = os.path.join(tmp_dir, "test_thread", ".omicsbase", "note-kernel", "kernel.pid")
            self.assertFalse(os.path.exists(kernel_pid_path))


if __name__ == "__main__":
    unittest.main()
