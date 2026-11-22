"""Heading ids, section addresses and the function map of the book index."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from engine.knowledge.book_urls import check_anchors, page_url, section_url, source_url
from engine.knowledge.indexer import (
    build_index, function_section_rows, index_package_docs, load_package_exports, rendered_files,
)
from engine.knowledge.qmd_parser import heading_identifier, iter_qmd_files, pandoc_identifier, parse_qmd
from engine.knowledge.search import search_bioc_knowledge

OMA = {
    "slug": "oma", "title": "Orchestrating Microbiome Analysis with Bioconductor", "short": "OMA",
    "repository": "https://github.com/microbiome/OMA", "commit": "abc123",
    "web": {"template": "https://bioconductor.org/books/release/OMA/{page}.html", "page": "path",
            "strip_prefix": "inst/"},
    "chapters_file": "inst/assets/_book.yml", "chapters_root": "inst",
}
OSCA_BASIC = {
    "slug": "osca-basic", "title": "Orchestrating Single-Cell Analysis: Basics",
    "repository": "https://github.com/OSCA-source/OSCA.basic", "commit": "def456",
    "web": {"template": "https://bioconductor.org/books/release/OSCA.basic/{page}.html", "page": "heading"},
    "exclude": ["vignettes/stub.Rmd"],
}
RFORMS = {
    "slug": "r-for-mass-spectrometry", "title": "R for Mass Spectrometry",
    "repository": "https://github.com/RforMassSpectrometry/book", "commit": "aaa111",
    "web": {"template": "https://rformassspectrometry.github.io/book/{page}.html", "page": "heading"},
    "exclude": ["lib/*"],
}
METABONAUT = {
    "slug": "metabonaut", "title": "Metabonaut",
    "repository": "https://github.com/rformassspectrometry/Metabonaut", "commit": "bbb222",
    "web": {"template": "https://rformassspectrometry.github.io/Metabonaut/articles/{page}.html", "page": "stem"},
}
TIDYOMICS = dict(METABONAUT, slug="tidyomics", web={
    "template": "https://tidyomics.github.io/tidyomicsWorkshop/articles/{page}.html", "page": "stem"})
RNASEQ = dict(METABONAUT, slug="rnaseq-gene", web={
    "template": "https://bioconductor.org/packages/release/workflows/vignettes/rnaseqGene/inst/doc/{page}.html",
    "page": "stem"})
MOFA2 = dict(METABONAUT, slug="mofa2", web={
    "template": "https://bioconductor.org/packages/release/bioc/vignettes/MOFA2/inst/doc/{page}.html",
    "page": "stem"})

ALPHA_QMD = """---
title: Alpha diversity
---
# Alpha diversity {#sec-alpha-diversity}

Background text.

## Estimation {#sec-estimate-diversity}

```{r}
#| label: alpha
library(mia)
library(scater)
tse <- addAlpha(tse, index = "shannon")
plotColData(tse, "shannon")
my_helper(tse)
assay(tse, "counts")
```

## Examples

First.

## Statistical testing & comparison

```{r}
res <- wilcox.test(shannon ~ group, data = df)
vegan::adonis2(d ~ group, data = df)
```

## Examples

Second.
"""
EXPORTS = {
    "mia": {"addAlpha", "transformAssay"},
    "scater": {"plotColData", "plotPCA"},
    "vegan": {"adonis2"},
    "stats": {"wilcox.test", "median"},
    "SummarizedExperiment": {"assay"},
    "base": {"library", "c"},
}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class AnchorTests(unittest.TestCase):
    def test_explicit_ids_are_kept(self):
        self.assertEqual(heading_identifier("Alpha diversity {#sec-alpha-diversity}"), "sec-alpha-diversity")
        self.assertEqual(heading_identifier("Clustering {.unnumbered #clust}"), "clust")
        self.assertEqual(heading_identifier("Raw MS data  {#sec-raw}"), "sec-raw")

    def test_pandoc_automatic_ids(self):
        cases = {
            "Note: Richness estimators and denoising": "note-richness-estimators-and-denoising",
            "Dogs?--in *my* house?": "dogs--in-my-house",
            "3. Applications": "applications",
            "Statistical testing & comparison": "statistical-testing-comparison",
            "[HTML], [S5], or [RTF]?": "html-s5-or-rtf",
            "Using `addAlpha()` with [mia](https://example.org)": "using-addalpha-with-mia",
            "Quality control, redux": "quality-control-redux",
            "Welcome {-}": "welcome",
            "123": "section",
        }
        for heading, identifier in cases.items():
            self.assertEqual(heading_identifier(heading), identifier, heading)
        self.assertEqual(pandoc_identifier("R 4.5 & Bioconductor_3.22"), "r-4.5-bioconductor_3.22")

    def test_blocks_carry_their_heading_and_page_ids(self):
        doc = parse_qmd(ALPHA_QMD, "inst/pages/alpha_diversity.qmd")
        self.assertEqual(doc.first_heading_anchor, "sec-alpha-diversity")
        by_heading = {}
        for block in doc.blocks:
            by_heading.setdefault(" > ".join(block.heading_path), []).append(block.anchor)
            self.assertEqual(block.page_anchor, "sec-alpha-diversity")
        self.assertEqual(by_heading["Alpha diversity"], ["sec-alpha-diversity"])
        self.assertEqual(by_heading["Alpha diversity > Estimation"], ["sec-estimate-diversity"])
        self.assertEqual(by_heading["Alpha diversity > Statistical testing & comparison"],
                         ["statistical-testing-comparison"])
        # Pandoc numbers a repeated automatic id.
        self.assertEqual(by_heading["Alpha diversity > Examples"], ["examples", "examples-1"])


class UrlTests(unittest.TestCase):
    def test_quarto_book_page_from_the_file(self):
        self.assertEqual(
            section_url(OMA, "inst/pages/alpha_diversity.qmd", "sec-estimate-diversity", "sec-alpha-diversity"),
            "https://bioconductor.org/books/release/OMA/pages/alpha_diversity.html#sec-estimate-diversity")
        self.assertEqual(page_url(OMA, "inst/index.qmd"), "https://bioconductor.org/books/release/OMA/index.html")

    def test_bookdown_page_from_the_first_heading(self):
        self.assertEqual(
            section_url(OSCA_BASIC, "inst/book/more-reddim.Rmd", "t-sne", "dimensionality-reduction-redux"),
            "https://bioconductor.org/books/release/OSCA.basic/dimensionality-reduction-redux.html#t-sne")
        self.assertEqual(section_url(OSCA_BASIC, "inst/book/index.Rmd", "welcome", "welcome"),
                         "https://bioconductor.org/books/release/OSCA.basic/index.html#welcome")
        # A block above the first heading falls back to the file's first heading.
        self.assertEqual(section_url(OSCA_BASIC, "inst/book/clustering.Rmd", "", "", "clustering"),
                         "https://bioconductor.org/books/release/OSCA.basic/clustering.html")

    def test_r_for_mass_spectrometry_page_from_the_explicit_id(self):
        self.assertEqual(section_url(RFORMS, "10-raw.Rmd", "what-is-raw-data", "sec-raw"),
                         "https://rformassspectrometry.github.io/book/sec-raw.html#what-is-raw-data")

    def test_pkgdown_and_vignette_pages_from_the_file_name(self):
        self.assertEqual(
            section_url(METABONAUT, "vignettes/a-end-to-end-untargeted-metabolomics.qmd", "introduction"),
            "https://rformassspectrometry.github.io/Metabonaut/articles/a-end-to-end-untargeted-metabolomics.html#introduction")
        self.assertEqual(page_url(TIDYOMICS, "vignettes/pseudobulk_transcriptomics.Rmd"),
                         "https://tidyomics.github.io/tidyomicsWorkshop/articles/pseudobulk_transcriptomics.html")
        self.assertEqual(page_url(RNASEQ, "vignettes/rnaseqGene.Rmd"),
                         "https://bioconductor.org/packages/release/workflows/vignettes/rnaseqGene/inst/doc/rnaseqGene.html")
        self.assertEqual(page_url(MOFA2, "vignettes/downstream_analysis.Rmd"),
                         "https://bioconductor.org/packages/release/bioc/vignettes/MOFA2/inst/doc/downstream_analysis.html")

    def test_no_template_falls_back_to_the_repository_file(self):
        book = {"slug": "x", "repository": "https://github.com/a/b", "commit": "c0ffee"}
        self.assertEqual(section_url(book, "vignettes/a b.Rmd", "intro"),
                         "https://github.com/a/b/blob/c0ffee/vignettes/a%20b.Rmd")
        self.assertEqual(section_url({"slug": "x"}, "a.Rmd", "intro"), "")

    def test_shipped_manifest_has_a_usable_template_for_every_book(self):
        manifest = json.loads((Path(__file__).resolve().parents[2] / "knowledge/sources.json").read_text())
        for book in manifest["books"]:
            url = page_url(book, "inst/pages/chapter.qmd", "first-heading")
            self.assertTrue(url.startswith("https://") and url.endswith(".html"), book["slug"])
            self.assertTrue(book["short"])
            self.assertIn(book["branch"], {"release", "main", "devel"})
        self.assertRegex(manifest["release_branch"], r"^RELEASE_\d+_\d+$")


class RenderedFileTests(unittest.TestCase):
    def test_quarto_chapter_list_and_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            write(repo / "inst/assets/_book.yml", (
                "book:\n  chapters:\n    - index.qmd\n    - part: Diversity\n      chapters:\n"
                "        - pages/alpha_diversity.qmd\n    - part: pages/part_page.qmd\n"
                "  appendices:\n    - pages/msea.qmd\n"))
            for name in ("inst/index.qmd", "inst/pages/alpha_diversity.qmd", "inst/pages/part_page.qmd",
                         "inst/pages/msea.qmd", "inst/pages/extra_material/todo.Rmd",
                         "PackageInstallations_Troubleshoots.qmd", "vignettes/stub.Rmd"):
                write(repo / name, "# Title\ntext\n")
            kept = rendered_files(OMA, repo, iter_qmd_files(repo))
            self.assertEqual(sorted(f.relative_to(repo).as_posix() for f in kept), [
                "inst/index.qmd", "inst/pages/alpha_diversity.qmd", "inst/pages/msea.qmd",
                "inst/pages/part_page.qmd"])
            # A missing chapter list keeps everything rather than emptying the book.
            (repo / "inst/assets/_book.yml").unlink()
            self.assertEqual(len(rendered_files(OMA, repo, iter_qmd_files(repo))), 7)

    def test_vendored_vignette_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for name in ("10-raw.Rmd", "lib/BiocManager/doc/BiocManager.Rmd"):
                write(repo / name, "# Title\ntext\n")
            kept = rendered_files(RFORMS, repo, iter_qmd_files(repo))
            self.assertEqual([f.name for f in kept], ["10-raw.Rmd"])


class FunctionMapTests(unittest.TestCase):
    def test_rows_keep_exported_analysis_functions_with_package_hints(self):
        doc = parse_qmd(ALPHA_QMD, "inst/pages/alpha_diversity.qmd")
        rows = set(function_section_rows(doc, EXPORTS))
        self.assertEqual(rows, {
            ("addAlpha", "mia", "Alpha diversity > Estimation"),
            ("plotColData", "scater", "Alpha diversity > Estimation"),
            # stats is not attached by the file: no hint, the name decides.
            ("wilcox.test", "", "Alpha diversity > Statistical testing & comparison"),
            ("adonis2", "vegan", "Alpha diversity > Statistical testing & comparison"),
        })

    def test_without_exports_every_attached_package_is_a_hint(self):
        doc = parse_qmd(ALPHA_QMD, "inst/pages/alpha_diversity.qmd")
        rows = set(function_section_rows(doc))
        self.assertIn(("addAlpha", "mia,scater", "Alpha diversity > Estimation"), rows)
        self.assertIn(("my_helper", "mia,scater", "Alpha diversity > Estimation"), rows)
        self.assertNotIn("assay", {name for name, _hint, _heading in rows})

    def test_exports_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package_exports.json"
            self.assertIsNone(load_package_exports(path))
            path.write_text(json.dumps({"packages": {"mia": ["addAlpha"], "empty": []}}))
            self.assertEqual(load_package_exports(path), {"mia": {"addAlpha"}, "empty": set()})


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ob-knowledge-index-test-")
        root = Path(self.temp.name)
        self.repos = root / "repos"
        write(self.repos / "oma/inst/assets/_book.yml", "book:\n  chapters:\n    - pages/alpha_diversity.qmd\n")
        write(self.repos / "oma/inst/pages/alpha_diversity.qmd", ALPHA_QMD)
        write(self.repos / "oma/inst/pages/extra_material/todo.Rmd", "# Todo\n```{r}\naddAlpha(x)\n```\n")
        write(self.repos / "osca-basic/inst/book/reduced-dimensions.Rmd", (
            "---\noutput: html_document\n---\n# Dimensionality reduction\n\nIntro.\n\n"
            "## Principal components analysis\n\n```{r}\nlibrary(scater)\nplotPCA(sce)\n```\n"))
        write(self.repos / "osca-basic/vignettes/stub.Rmd", "# Stub\n```{r}\nplotPCA(sce)\n```\n")
        self.db = root / "knowledge.db"
        self.books = [OMA, OSCA_BASIC]
        self.stats = build_index(self.repos, self.db, strict=True, books=self.books, exports=EXPORTS)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, sql):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(sql).fetchall()

    def test_function_sections_cover_rendered_book_files_only(self):
        rows = self.rows("SELECT fn, pkg_hint, book_slug, heading_path, source_file, anchor, url "
                         "FROM function_sections ORDER BY book_slug, fn")
        self.assertEqual([r[0] for r in rows], ["addAlpha", "adonis2", "plotColData", "wilcox.test", "plotPCA"])
        self.assertEqual(rows[0], (
            "addAlpha", "mia", "oma", "Alpha diversity > Estimation", "inst/pages/alpha_diversity.qmd",
            "sec-estimate-diversity",
            "https://bioconductor.org/books/release/OMA/pages/alpha_diversity.html#sec-estimate-diversity"))
        self.assertEqual(rows[-1][1:], (
            "scater", "osca-basic", "Dimensionality reduction > Principal components analysis",
            "inst/book/reduced-dimensions.Rmd", "principal-components-analysis",
            "https://bioconductor.org/books/release/OSCA.basic/dimensionality-reduction.html"
            "#principal-components-analysis"))
        self.assertEqual(self.rows("SELECT DISTINCT source_file FROM bioc_knowledge ORDER BY 1"),
                         [("inst/book/reduced-dimensions.Rmd",), ("inst/pages/alpha_diversity.qmd",)])

    def test_search_returns_the_section_address(self):
        result = search_bioc_knowledge("shannon addAlpha", db_path=self.db)
        match = result["matches"][0]
        self.assertEqual(match["anchor"], "sec-estimate-diversity")
        self.assertTrue(match["url"].endswith("alpha_diversity.html#sec-estimate-diversity"))
        self.assertIn("Alpha diversity", match["heading"])

    def test_package_help_is_not_in_the_map_and_links_the_package_page(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE knowledge_sources (book_slug TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
            conn.execute("INSERT INTO knowledge_sources VALUES (?, ?)", ("oma", json.dumps(
                dict(OMA, attribution="Fixture authors", license_statements=[]))))
        docs = Path(self.temp.name) / "package_docs.json"
        docs.write_text(json.dumps([{
            "package": "mia", "version": "1.18.0", "title": "Microbiome analysis", "license": "Artistic-2.0",
            "url": "https://bioconductor.org/packages/release/bioc/html/mia.html",
            "topics": [{"name": "addAlpha", "title": "Estimate alpha diversity", "aliases": ["addAlpha"],
                        "prose": "Zebrafinch estimate.", "code": "tse <- addAlpha(tse)", "rd_file": "addAlpha.Rd"}],
            "vignettes": [],
        }]))
        before = self.rows("SELECT count(*) FROM function_sections")
        index_package_docs(docs, self.db)
        self.assertEqual(self.rows("SELECT count(*) FROM function_sections"), before)
        match = search_bioc_knowledge("zebrafinch", db_path=self.db)["matches"][0]
        self.assertEqual(match["book"], "pkg:mia")
        self.assertEqual(match["source_url"], "https://bioconductor.org/packages/release/bioc/html/mia.html")
        self.assertNotIn("/blob/", search_bioc_knowledge("zebrafinch", db_path=self.db)["markdown"])
        book = search_bioc_knowledge("shannon addAlpha", book="oma", db_path=self.db)["matches"][0]
        self.assertEqual(book["source_url"],
                         "https://github.com/microbiome/OMA/blob/abc123/inst/pages/alpha_diversity.qmd")

    def test_search_works_on_an_index_without_the_side_tables(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE sections")
        match = search_bioc_knowledge("shannon addAlpha", db_path=self.db)["matches"][0]
        self.assertNotIn("url", match)

    def test_anchor_check_keeps_live_anchors_and_falls_back(self):
        oma_page = "https://bioconductor.org/books/release/OMA/pages/alpha_diversity.html"
        fetched = []

        def fetch(url):
            fetched.append(url)
            # The OMA page lost one heading; the OSCA page cannot be read.
            return {"sec-alpha-diversity", "sec-estimate-diversity", "examples"} if url == oma_page else None

        counts = check_anchors(self.db, self.books, fetch=fetch, workers=1)
        self.assertEqual(sorted(fetched), sorted(set(fetched)))
        self.assertEqual(len(fetched), 2)
        urls = dict(self.rows("SELECT fn, url FROM function_sections"))
        self.assertEqual(urls["addAlpha"], oma_page + "#sec-estimate-diversity")
        self.assertEqual(urls["wilcox.test"], oma_page)
        self.assertEqual(urls["plotPCA"],
                         "https://github.com/OSCA-source/OSCA.basic/blob/def456/inst/book/reduced-dimensions.Rmd")
        sections = dict(self.rows("SELECT heading_path, url FROM sections WHERE book_slug = 'oma'"))
        self.assertEqual(sections["Alpha diversity > Examples"], oma_page + "#examples")
        self.assertGreater(counts["kept"], 0)
        self.assertGreater(counts["page"], 0)
        self.assertGreater(counts["source"], 0)


class PinTests(unittest.TestCase):
    def test_update_pins_follows_branches_without_fetching_sources(self):
        from scripts.setup_knowledge import book_branch, update_pins

        manifest = {"release_branch": "RELEASE_3_23", "books": [
            {"slug": "oma", "repository": "https://github.com/microbiome/OMA", "commit": "old1",
             "branch": "release", "fetch_repository": "https://git.bioconductor.org/packages/OMA",
             "license_statements": [{"url": "https://github.com/microbiome/OMA/blob/old1/DESCRIPTION"}]},
            {"slug": "metabonaut", "repository": "https://github.com/x/Metabonaut", "commit": "same", "branch": "devel"},
            {"slug": "pinned", "repository": "https://github.com/x/pinned", "commit": "fixed"},
        ]}
        asked = []

        def resolve(repository, branch):
            asked.append((repository, branch))
            return {"RELEASE_3_23": "new1", "devel": "same"}[branch]

        moved = update_pins(manifest, resolve)
        self.assertEqual(moved, {"oma": ("old1", "new1")})
        self.assertEqual(asked, [("https://git.bioconductor.org/packages/OMA", "RELEASE_3_23"),
                                 ("https://github.com/x/Metabonaut", "devel")])
        self.assertEqual(manifest["books"][0]["commit"], "new1")
        self.assertIn("/blob/new1/", manifest["books"][0]["license_statements"][0]["url"])
        self.assertEqual(manifest["books"][2]["commit"], "fixed")
        self.assertIsNone(book_branch(manifest["books"][2], manifest))


if __name__ == "__main__":
    unittest.main()
