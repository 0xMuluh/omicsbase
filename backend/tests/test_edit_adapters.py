"""Editor and repair adapters preserve the engine's transaction contract."""

from __future__ import annotations

from app.services.editor import _apply_edits
from app.services.repair import _apply_line_repairs


def test_editor_mixed_payload_aborts_without_partial_write(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\n")

    result = _apply_edits(
        tmp_path,
        [
            {"path": "code/analysis.R", "search": "alpha <- 1", "replace": "alpha <- 2"},
            {"path": "code/analysis.R", "search": "missing", "replace": "never"},
        ],
    )

    assert not any(item.ok for item in result)
    assert target.read_text() == "alpha <- 1\n"


def test_editor_accepts_embedded_path_patch_without_outer_path(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\n")
    patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-alpha <- 1
+alpha <- 3
*** End Patch"""

    result = _apply_edits(tmp_path, [{"patch": patch}])

    assert any(item.ok for item in result)
    assert target.read_text() == "alpha <- 3\n"


def test_editor_refuses_full_rewrite_of_truncated_source(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    original = "x <- 1\n" * 3000
    target.write_text(original)

    result = _apply_edits(tmp_path, [{"path": "code/analysis.R", "content": "x <- 2\n"}])

    assert not any(item.ok for item in result)
    assert target.read_text() == original
    assert any("truncated" in " ".join(item.diagnostics).lower() for item in result)


def test_repair_line_target_requires_current_hash(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\n")

    result = _apply_line_repairs(
        tmp_path,
        [{
            "path": "code/analysis.R",
            "line": 1,
            "replacement": "alpha <- 2",
            "base_sha256": "0" * 64,
        }],
    )

    assert not any(item.ok for item in result)
    assert target.read_text() == "alpha <- 1\n"
    assert any(item.strategy == "conflict" for item in result)
