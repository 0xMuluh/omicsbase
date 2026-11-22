"""The book badge: which functions count, and when a cell is 'from the book'."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine.kernel.note_kernel import build_kernel_script
from engine.knowledge import book_refs
from engine.knowledge.book_refs import (
    classify, extract_library_packages, extract_r_calls, log_lookups, match, read_lookups,
)

OMA_URL = "https://bioconductor.org/books/release/OMA/pages/alpha_diversity.html#sec-estimate-diversity"
SECTIONS = [
    # fn, pkg_hint, book_slug, heading_path, source_file, anchor, url
    ("addAlpha", "mia", "oma", "Alpha diversity > Estimation", "inst/pages/alpha_diversity.qmd",
     "sec-estimate-diversity", OMA_URL),
    ("plotColData", "scater", "oma", "Alpha diversity > Estimation", "inst/pages/alpha_diversity.qmd",
     "sec-estimate-diversity", OMA_URL),
    ("plotColData", "scater", "oma", "Alpha diversity > Visualisation", "inst/pages/alpha_diversity.qmd",
     "visualisation", "https://bioconductor.org/books/release/OMA/pages/alpha_diversity.html#visualisation"),
    ("addAlpha", "mia", "osca", "Diversity", "inst/book/diversity.Rmd", "diversity",
     "https://bioconductor.org/books/release/OSCA/diversity.html#diversity"),
    ("plotPCA", "DESeq2", "rnaseq-gene", "Exploratory analysis > PCA plot", "vignettes/rnaseqGene.Rmd",
     "pca-plot", "https://example.org/rnaseqGene.html#pca-plot"),
    ("plotPCA", "scater", "osca-basic", "Dimensionality reduction > PCA", "inst/book/reduced-dimensions.Rmd",
     "pca", "https://example.org/osca/dimensionality-reduction.html#pca"),
    ("wilcox.test", "", "oma", "Differential abundance > Wilcoxon", "inst/pages/differential_abundance.qmd",
     "wilcoxon", "https://example.org/da.html#wilcoxon"),
]
BOOKS = {
    "oma": {"title": "Orchestrating Microbiome Analysis with Bioconductor", "short": "OMA",
            "commit": "22d30d95bd3994e8838a1b9401e9e70563f316de", "version": "1.0.0"},
    "osca": {"title": "Orchestrating Single-Cell Analysis with Bioconductor",
             "commit": "b1d002f82d1d17286a889c08b31f36c19bd83c15"},
    "rnaseq-gene": {"title": "RNA-Seq Workflow", "short": "rnaseqGene", "commit": "0d7e27dde3ca"},
    "osca-basic": {"title": "OSCA: Basics", "short": "OSCA Basics", "commit": "e82ba056962c"},
}


def make_db(path, with_map=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE knowledge_sources (book_slug TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
    conn.executemany("INSERT INTO knowledge_sources VALUES (?, ?)",
                     [(slug, json.dumps(book)) for slug, book in BOOKS.items()])
    if with_map:
        conn.execute("CREATE TABLE function_sections (fn, pkg_hint, book_slug, heading_path, source_file, anchor, url)")
        conn.executemany("INSERT INTO function_sections VALUES (?, ?, ?, ?, ?, ?, ?)", SECTIONS)
    conn.commit()
    conn.close()
    return path


def call(name, package=None, generic=False):
    record = {"name": name, "generic": generic}
    if package:
        record["package"] = package
    return record


class ExtractionTests(unittest.TestCase):
    def test_calls_skip_strings_comments_options_and_keywords(self):
        code = (
            "#| label: fig-alpha\n"
            "#| fig-cap: \"calls plotNothing(x) in a caption\"\n"
            "library(mia)  # commented(call)\n"
            "tse <- addAlpha(tse, index = \"shannon_diversity(x)\")\n"
            "if (ncol(tse) > 1) { for (i in seq_len(3)) print(i) }\n"
            "res <- vegan::adonis2(dist ~ group, data = df)\n"
            "x <- obj$method(1); y <- obj@slot(2)\n"
            "f <- function(a) stats:::anova.lm(a)\n"
            "`my fun`(1)\n"
        )
        calls = extract_r_calls(code)
        self.assertIn((None, "addAlpha"), calls)
        self.assertIn(("vegan", "adonis2"), calls)
        self.assertIn(("stats", "anova.lm"), calls)
        names = {name for _package, name in calls}
        self.assertFalse(names & {"plotNothing", "commented", "shannon_diversity", "if", "for",
                                  "function", "method", "slot"})
        self.assertNotIn((None, "adonis2"), calls)

    def test_library_packages(self):
        code = (
            "library(mia)\nsuppressPackageStartupMessages(library(\"scater\"))\n"
            "require(vegan, quietly = TRUE)\n# library(notLoaded)\nrequireNamespace('ALDEx2')\n"
        )
        self.assertEqual(extract_library_packages(code), ["mia", "scater", "vegan", "ALDEx2"])


class ClassificationTests(unittest.TestCase):
    def test_kinds(self):
        cases = {
            ("addAlpha", "mia"): "analysis",
            ("LimROTS", "LimROTS"): "analysis",
            ("somethingNew", "packageNobodyKnows"): "analysis",
            ("helper", "R_GlobalEnv"): "user",
            (".note_display", "R_GlobalEnv"): "neutral",
            ("paste0", "base"): "neutral",
            ("ggplot", "ggplot2"): "neutral",
            ("mutate", "dplyr"): "neutral",
            ("assay", "SummarizedExperiment"): "neutral",
            ("TreeSummarizedExperiment", "TreeSummarizedExperiment"): "neutral",
            ("importQIIME2", "mia"): "neutral",
            ("convertToPhyloseq", "mia"): "neutral",
            ("otu_table", "phyloseq"): "neutral",
            ("ordinate", "phyloseq"): "analysis",
            ("median", "stats"): "neutral",
            ("wilcox.test", "stats"): "analysis",
            ("p.adjust", "stats"): "analysis",
            ("colnames", "BiocGenerics"): "neutral",
            ("counts", "BiocGenerics"): "neutral",
            ("plotPCA", "BiocGenerics"): "analysis",
            ("runPCA", "BiocSingular"): "analysis",
            ("localThing", None): "neutral",
        }
        for (name, package), kind in cases.items():
            self.assertEqual(classify(name, package), kind, (name, package))


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-book-refs-test-")
        self.db = make_db(os.path.join(self.temp.name, "knowledge.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_from_book_prefers_oma_and_widest_section(self):
        calls = {"calls": [call("library", "base"), call("addAlpha", "mia", True),
                           call("plotColData", "scater"), call("assay", "SummarizedExperiment", True)],
                 "attached": ["mia", "scater", "stats", "base"]}
        refs = match(calls, self.db)
        self.assertEqual(refs["status"], "from_book")
        self.assertEqual(refs["missing"], [])
        self.assertEqual(refs["shown"], {
            "function": "addAlpha", "package": "mia", "book": "oma", "book_short": "OMA",
            "book_title": "Orchestrating Microbiome Analysis with Bioconductor",
            "chapter": "Alpha diversity", "heading": "Estimation", "url": OMA_URL, "looked_up": False,
        })
        self.assertEqual(len(refs["refs"]), 4)
        self.assertEqual(set(refs["refs"][0]), set(refs["shown"]))
        self.assertEqual(refs["book_versions"], {"oma": "1.0.0", "osca": "b1d002f82d1d"})
        json.dumps(refs)

    def test_looked_up_section_wins(self):
        looked_up = [{"book": "osca", "heading_path": "Diversity", "source_file": "inst/book/diversity.Rmd",
                      "anchor": "diversity"}]
        refs = match([call("addAlpha", "mia")], self.db, looked_up)
        self.assertEqual(refs["shown"]["book"], "osca")
        self.assertEqual(refs["shown"]["book_short"], "OSCA")
        self.assertTrue(refs["shown"]["looked_up"])
        self.assertEqual([r["looked_up"] for r in refs["refs"]], [True, False])

    def test_not_in_books_lists_missing_and_keeps_other_matches(self):
        refs = match([call("addAlpha", "mia"), call("LimROTS", "LimROTS")], self.db)
        self.assertEqual(refs["status"], "not_in_books")
        self.assertIsNone(refs["shown"])
        self.assertEqual(refs["missing"], [{"function": "LimROTS", "package": "LimROTS"}])
        self.assertEqual({r["function"] for r in refs["refs"]}, {"addAlpha"})

    def test_user_defined_function_gives_nothing(self):
        self.assertIsNone(match([call("addAlpha", "mia"), call("my_helper", "R_GlobalEnv")], self.db))

    def test_base_and_accessors_only_give_nothing(self):
        calls = [call("paste0", "base"), call("head", "utils"), call("assay", "SummarizedExperiment"),
                 call("ggplot", "ggplot2"), call("median", "stats"), call("unknownLocal")]
        self.assertIsNone(match(calls, self.db))
        self.assertIsNone(match({"calls": [], "attached": []}, self.db))

    def test_generic_hosted_in_biocgenerics_follows_the_session(self):
        plot_pca = call("plotPCA", "BiocGenerics", True)
        deseq = match({"calls": [plot_pca], "attached": ["DESeq2", "BiocGenerics", "stats"]}, self.db)
        self.assertEqual(deseq["status"], "from_book")
        self.assertEqual([r["book"] for r in deseq["refs"]], ["rnaseq-gene"])
        scater = match({"calls": [plot_pca], "attached": ["scater", "BiocGenerics"]}, self.db)
        self.assertEqual([r["book"] for r in scater["refs"]], ["osca-basic"])
        # The cell's own packages count too.
        same_cell = match({"calls": [plot_pca, call("DESeq", "DESeq2")], "attached": []}, self.db)
        self.assertEqual([r["book"] for r in same_cell["refs"]], ["rnaseq-gene"])
        self.assertEqual(same_cell["missing"], [{"function": "DESeq", "package": "DESeq2"}])
        neither = match({"calls": [plot_pca], "attached": ["BiocGenerics"]}, self.db)
        self.assertEqual(neither["status"], "not_in_books")
        self.assertEqual(neither["missing"], [{"function": "plotPCA", "package": "BiocGenerics"}])

    def test_package_hint_keeps_namesakes_apart(self):
        refs = match([call("plotPCA", "PCAtools")], self.db)
        self.assertEqual(refs["status"], "not_in_books")
        self.assertEqual(refs["refs"], [])

    def test_stats_test_without_hint_matches_by_name(self):
        refs = match([call("wilcox.test", "stats")], self.db)
        self.assertEqual(refs["status"], "from_book")
        self.assertEqual(refs["shown"]["heading"], "Wilcoxon")

    def test_index_without_map_or_missing_index_gives_nothing(self):
        old = make_db(os.path.join(self.temp.name, "old.db"), with_map=False)
        self.assertIsNone(match([call("addAlpha", "mia")], old))
        self.assertIsNone(match([call("addAlpha", "mia")], os.path.join(self.temp.name, "absent.db")))

    def test_run_reads_calls_json_and_lookups(self):
        note = Path(self.temp.name) / "note1"
        run = note / "runs" / "cell_1"
        run.mkdir(parents=True)
        self.assertIsNone(book_refs.book_refs_for_run(run, note, self.db))
        (run / "calls.json").write_text(json.dumps({"calls": [call("addAlpha", "mia")], "attached": ["mia"]}))
        log_lookups(note, [{"book": "osca", "heading": "Diversity", "source_file": "inst/book/diversity.Rmd"}])
        refs = book_refs.book_refs_for_run(run, note, self.db)
        self.assertEqual(refs["shown"]["book"], "osca")


class LookupLogTests(unittest.TestCase):
    def test_log_appends_book_sections_only(self):
        with tempfile.TemporaryDirectory() as note:
            self.assertEqual(read_lookups(note), [])
            written = log_lookups(note, [
                {"book": "oma", "heading": "Alpha diversity > Estimation",
                 "source_file": "inst/pages/alpha_diversity.qmd", "anchor": "sec-estimate-diversity"},
                {"book": "pkg:mia", "heading": "mia > addAlpha", "source_file": "man/addAlpha.Rd"},
            ])
            self.assertEqual(written, 1)
            log_lookups(note, [{"book": "osca", "heading": "Diversity", "source_file": "d.Rmd"}])
            path = Path(note) / ".omicsbase" / "knowledge-lookups.jsonl"
            self.assertEqual(len(path.read_text().splitlines()), 2)
            self.assertEqual(read_lookups(note)[0], {
                "book": "oma", "heading_path": "Alpha diversity > Estimation",
                "source_file": "inst/pages/alpha_diversity.qmd", "anchor": "sec-estimate-diversity",
            })
            with open(path, "a") as handle:
                handle.write("not json\n")
            self.assertEqual(len(read_lookups(note)), 2)


class ExecutorTests(unittest.TestCase):
    def run_cell(self, projects, calls, events=""):
        from engine.kernel import executor

        def fake_request(handle, request_id, run_dir_rel, source, timeout_seconds, cancel_check, on_phase=None):
            run = Path(projects) / "note-1" / run_dir_rel
            (run / executor.EVENTS_FILE_NAME).write_text(events)
            if calls is not None:
                (run / "calls.json").write_text(json.dumps(calls))
            return {"id": request_id}

        db = make_db(os.path.join(projects, "knowledge.db"))
        with patch.object(executor, "ensure_kernel", return_value=object()), \
                patch.object(executor, "request_cell", side_effect=fake_request), \
                patch.object(executor, "KNOWLEDGE_DB_PATH", db):
            return executor.execute_note_cell(projects, "note-1", "tse <- addAlpha(tse)")

    def test_result_carries_book_refs_only_for_a_clean_analysis_cell(self):
        calls = {"calls": [call("addAlpha", "mia", True)], "attached": ["mia"]}
        with tempfile.TemporaryDirectory() as projects:
            result = self.run_cell(projects, calls)
            self.assertTrue(result["success"])
            self.assertEqual(result["book_refs"]["status"], "from_book")
        with tempfile.TemporaryDirectory() as projects:
            failed = self.run_cell(projects, calls, json.dumps({"type": "error", "content": "boom"}) + "\n")
            self.assertFalse(failed["success"])
            self.assertNotIn("book_refs", failed)
        with tempfile.TemporaryDirectory() as projects:
            self.assertNotIn("book_refs", self.run_cell(projects, {"calls": [call("paste", "base")]}))
        with tempfile.TemporaryDirectory() as projects:
            self.assertNotIn("book_refs", self.run_cell(projects, None))
        with tempfile.TemporaryDirectory() as projects:
            self.assertTrue(self.run_cell(projects, "not a calls object")["success"])


class KernelScriptTests(unittest.TestCase):
    def test_script_writes_calls_after_an_evaluated_cell(self):
        script = build_kernel_script()
        self.assertIn(".note_calls <- function(run_dir, source) {", script)
        self.assertIn("if (.note_evaluated) .note_calls(run_dir, source)", script)
        self.assertIn("file.path(run_dir, 'calls.json')", script)
        self.assertEqual(script.count("{"), script.count("}"))


if __name__ == "__main__":
    unittest.main()
