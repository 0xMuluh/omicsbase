"""Tests for post-render reviewer."""

from pathlib import Path

from app.services.reviewer import review_render_output


def test_review_fails_without_output(tmp_path: Path):
    result = review_render_output(str(tmp_path))
    assert result["status"] == "failed"
    assert "output directory" in result["summary"].lower()


def test_review_passes_minimal_project(tmp_path: Path):
    base = tmp_path
    code_dir = base / "code"
    code_dir.mkdir()
    (code_dir / "data.R").write_text("x <- 1\n")
    (code_dir / "funct.R").write_text("f <- function(x) x\n")
    (code_dir / "main.R").write_text("source('data.R')\n")
    (code_dir / "_quarto.yml").write_text("project:\n  type: website\n")
    (code_dir / "index.qmd").write_text("---\ntitle: Test\n---\n\n# Hello\n\n```{r}\nsessionInfo()\n```\n")

    output_dir = base / "output"
    output_dir.mkdir()
    (output_dir / "index.html").write_text(
        "<html><body><nav>menu</nav><main>sessionInfo output</main></body></html>" * 20
    )

    result = review_render_output(str(base))
    assert result["status"] in {"passed", "warning"}
    assert any(check["name"] == "qmd_pages" and check["status"] == "passed" for check in result["checks"])


def test_review_passes_agent_built_layout_without_legacy_quartet(tmp_path: Path):
    """OpenHands may invent numbered pages; review must not demand data.R/main.R."""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "_quarto.yml").write_text("project:\n  type: website\n")
    (code_dir / "01_overview.qmd").write_text(
        "---\ntitle: Overview\n---\n\n# Overview\n\n```{r}\nsessionInfo()\n```\n"
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "index.html").write_text(
        "<html><body><nav>menu</nav><main>sessionInfo output</main></body></html>" * 20
    )

    result = review_render_output(str(tmp_path))
    assert result["status"] in {"passed", "warning"}, result
    assert not any(
        check["status"] == "failed" and "data.R" in check.get("detail", "")
        for check in result["checks"]
    )


def test_review_fails_malformed_quarto_frontmatter(tmp_path: Path):
    base = tmp_path
    code_dir = base / "code"
    code_dir.mkdir()
    (code_dir / "data.R").write_text("x <- 1\n")
    (code_dir / "funct.R").write_text("f <- function(x) x\n")
    (code_dir / "main.R").write_text("source('data.R')\n")
    (code_dir / "_quarto.yml").write_text("project:\n  type: website\n")
    (code_dir / "index.qmd").write_text("---\ntitle: [broken\n---\n# Bad\n")
    output_dir = base / "output"
    output_dir.mkdir()
    (output_dir / "index.html").write_text("<html><body><nav>menu</nav><main>sessionInfo</main></body></html>" * 20)

    result = review_render_output(str(base))
    assert result["status"] == "failed"
    check = next(item for item in result["checks"] if item["name"] == "quarto_semantics")
    assert check["status"] == "failed"
