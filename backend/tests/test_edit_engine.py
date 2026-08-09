"""Focused tests for the transactional edit engine."""

from __future__ import annotations

import hashlib

import pytest

from app.services.edit_engine import (
    EditConflict,
    EditMatchError,
    EditPolicyError,
    EditOperation,
    EditPolicy,
    apply_transaction,
    commit_transaction,
    parse_apply_patch,
    prepare_transaction,
    revert_transaction,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_prepare_is_all_or_nothing(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\nbeta <- 2\n")
    original = target.read_bytes()

    with pytest.raises(EditMatchError):
        prepare_transaction(
            project,
            [
                {"path": "code/analysis.R", "kind": "replace", "search": "alpha <- 1", "replace": "alpha <- 9"},
                {"path": "code/analysis.R", "kind": "replace", "search": "missing", "replace": "never"},
            ],
        )

    assert target.read_bytes() == original


def test_patch_envelope_is_prepared_and_committed(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\nbeta <- 2\n")
    patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-alpha <- 1
+alpha <- 3
*** End Patch"""

    parsed = parse_apply_patch(patch)
    assert parsed[0].kind == "patch_hunks"
    result = apply_transaction(project, [{"kind": "patch", "patch": patch}], origin="test")
    assert result.status == "committed"
    assert target.read_text() == "alpha <- 3\nbeta <- 2\n"
    assert (project / ".omicsbase" / "edits" / result.transaction_id / "manifest.json").is_file()
    invalidation = (project / ".omicsbase" / "invalidation.json").read_text()
    assert "code/analysis.R" in invalidation


def test_pending_invalidation_accumulates_successive_edits(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    first = project / "code" / "one.R"
    second = project / "code" / "two.R"
    first.write_text("x <- 1\n")
    second.write_text("y <- 1\n")
    apply_transaction(
        project,
        [{"path": "code/one.R", "kind": "replace", "search": "1", "replace": "2", "base_sha256": _sha(first.read_bytes())}],
        origin="test",
    )
    apply_transaction(
        project,
        [{"path": "code/two.R", "kind": "replace", "search": "1", "replace": "2", "base_sha256": _sha(second.read_bytes())}],
        origin="test",
    )
    payload = __import__("json").loads((project / ".omicsbase" / "invalidation.json").read_text())
    assert payload["changed_paths"] == ["code/one.R", "code/two.R"]


def test_patch_end_of_file_marker_anchors_context(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\nbeta <- 2\n")
    patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-beta <- 2
+beta <- 3
*** End of File
*** End Patch"""
    result = apply_transaction(project, [{"kind": "patch", "patch": patch}], origin="test")
    assert result.status == "committed"
    assert target.read_text() == "alpha <- 1\nbeta <- 3\n"

    middle_patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-alpha <- 1
+alpha <- 4
*** End of File
*** End Patch"""
    with pytest.raises(EditMatchError, match="End of File"):
        prepare_transaction(project, [{"kind": "patch", "patch": middle_patch}])


def test_stale_base_is_rejected_at_commit(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\n")
    before = target.read_bytes()
    prepared = prepare_transaction(
        project,
        [EditOperation(path="code/analysis.R", kind="replace", search="1", replace="2", base_sha256=_sha(before))],
    )
    target.write_text("alpha <- 7\n")

    with pytest.raises(EditConflict):
        commit_transaction(prepared)

    assert target.read_text() == "alpha <- 7\n"


def test_duplicate_match_requires_explicit_opt_in(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("x <- 1\nx <- 1\n")

    with pytest.raises(EditMatchError):
        prepare_transaction(
            project,
            [{"path": "code/analysis.R", "kind": "replace", "search": "x <- 1", "replace": "x <- 2"}],
        )


def test_indent_and_unicode_matching_are_unique(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("  if (ok) {\n    print(“old”)\n  }\n")
    prepared = prepare_transaction(
        project,
        [
            {
                "path": "code/analysis.R",
                "kind": "replace",
                "search": 'if (ok) {\n  print("old")',
                "replace": 'if (ok) {\n  print("new")',
            }
        ],
    )
    assert prepared.files[0].strategies == ["indent_flexible"]
    commit_transaction(prepared)
    assert 'print("new")' in target.read_text()


def test_explicit_elision_anchor_is_unique_and_conservative(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("start <- 1\nkeep <- TRUE\nold <- 2\nend <- 3\n")
    prepared = prepare_transaction(
        project,
        [{"path": "code/analysis.R", "kind": "replace", "search": "start <- 1\n...\nend <- 3", "replace": "start <- 9\n...\nend <- 3"}],
    )
    assert prepared.files[0].strategies == ["elision_anchor"]
    commit_transaction(prepared)
    assert target.read_text().startswith("start <- 9\n")


def test_failed_match_reports_cross_file_candidates(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    (project / "code" / "one.R").write_text("shared <- TRUE\n")
    target = project / "code" / "two.R"
    target.write_text("different <- TRUE\n")
    with pytest.raises(EditMatchError) as error:
        prepare_transaction(project, [{"path": "code/two.R", "kind": "replace", "search": "shared <- TRUE", "replace": "shared <- FALSE"}])
    assert error.value.details["cross_file_candidates"] == ["code/one.R"]


def test_commit_rechecks_lock_added_after_prepare(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\n")
    prepared = prepare_transaction(
        project,
        [EditOperation(path="code/analysis.R", kind="rewrite", content="alpha <- 2\n", base_sha256=_sha(target.read_bytes()))],
    )
    locks_dir = project / ".omicsbase"
    locks_dir.mkdir()
    (locks_dir / "locks.json").write_text('{"paths": ["code/analysis.R"]}')

    with pytest.raises(EditPolicyError, match="locked"):
        commit_transaction(prepared)
    assert target.read_text() == "alpha <- 1\n"


def test_revert_requires_current_after_hash_and_restores_before(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("alpha <- 1\n")
    result = apply_transaction(
        project,
        [{"path": "code/analysis.R", "kind": "replace", "search": "1", "replace": "2", "base_sha256": _sha(target.read_bytes())}],
        origin="test",
    )

    reverted = revert_transaction(project, result.transaction_id)
    assert reverted.status == "committed"
    assert target.read_text() == "alpha <- 1\n"



def _write_invalidation_pack(project):
    # Keep the fixture intentionally small while exercising a declared source,
    # validator, capability binding, and a page that is render-only.
    (project / "code").mkdir(parents=True)
    (project / "output").mkdir()
    (project / "code" / "fit.R").write_text("fit <- 1\n")
    (project / "code" / "validate.R").write_text("stopifnot(TRUE)\n")
    (project / "code" / "page.qmd").write_text("---\ntitle: Page\n---\n# Page\n")
    (project / "report_pack.yaml").write_text(
        """schema_version: \"1.0\"\nid: demo-pack\nversion: \"1.0\"\ndomain: demo\nname: Demo\ndefault_adaptation: inspect\nfile_rules:\n  - id: validator\n    match: code/validate.R\n    role: validator\n    adaptation: inspect\nexecution:\n  working_directory: code\n  render: incremental\n  steps:\n    - id: fit\n      path: code/fit.R\n      role: analysis\n    - id: validate\n      path: code/validate.R\n      role: validator\n  artifacts:\n    - output/index.html\ncapabilities:\n  demo-analysis:\n    sources:\n      - code/fit.R\n    execution_steps:\n      - fit\n      - validate\n    parameters: {}\n    outputs:\n      - output/index.html\n    validators:\n      - code/validate.R\n""",
        encoding="utf-8",
    )


def test_pending_invalidation_uses_report_pack_capability_impact(tmp_path):
    project = tmp_path / "project"
    _write_invalidation_pack(project)
    fit = project / "code" / "fit.R"
    apply_transaction(
        project,
        [{
            "path": "code/fit.R",
            "kind": "replace",
            "search": "fit <- 1",
            "replace": "fit <- 2",
            "base_sha256": _sha(fit.read_bytes()),
        }],
        origin="test",
    )
    payload = __import__("json").loads((project / ".omicsbase" / "invalidation.json").read_text())
    assert payload["source"] == "report_pack"
    assert payload["impacted_capabilities"] == ["demo-analysis"]
    assert payload["resume_from_step"] == "fit"
    assert payload["invalidated_steps"] == ["fit", "validate"]
    assert payload["earliest_step_index"] == 0
    assert payload["full_workflow_invalidated"] is True


def test_report_pack_page_edit_is_targeted_without_data_rerun(tmp_path):
    project = tmp_path / "project"
    _write_invalidation_pack(project)
    page = project / "code" / "page.qmd"
    apply_transaction(
        project,
        [{
            "path": "code/page.qmd",
            "kind": "replace",
            "search": "# Page",
            "replace": "# Updated page",
            "base_sha256": _sha(page.read_bytes()),
        }],
        origin="test",
    )
    payload = __import__("json").loads((project / ".omicsbase" / "invalidation.json").read_text())
    assert payload["source"] == "report_pack"
    assert payload["impacted_capabilities"] == []
    assert payload["resume_from_step"] is None
    assert payload["invalidated_steps"] == []
    assert payload["targeted_pages"] == ["page.qmd"]



def test_patch_golden_multi_hunk_update_preserves_unrelated_lines(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_text("one <- 1\ntwo <- 2\nthree <- 3\nfour <- 4\nfive <- 5\n")
    patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-one <- 1
+one <- 10
@@
-four <- 4
+four <- 40
*** End Patch"""

    result = apply_transaction(project, [{"kind": "patch", "patch": patch}], origin="golden")
    assert result.status == "committed"
    assert target.read_text() == "one <- 10\ntwo <- 2\nthree <- 3\nfour <- 40\nfive <- 5\n"


def test_patch_golden_add_and_delete_are_atomic(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    old = project / "code" / "old.R"
    old.write_text("old <- TRUE\n")
    patch = """*** Begin Patch
*** Add File: code/new.R
+new <- TRUE
*** Delete File: code/old.R
*** End Patch"""

    result = apply_transaction(
        project,
        [{"kind": "patch", "patch": patch}],
        origin="golden",
        policy=EditPolicy(allow_create=True, allow_delete=True),
    )
    assert result.status == "committed"
    assert (project / "code" / "new.R").read_text() == "new <- TRUE\n"
    assert not old.exists()


def test_patch_golden_no_final_newline_marker_is_honored(tmp_path):
    project = tmp_path / "project"
    (project / "code").mkdir(parents=True)
    target = project / "code" / "analysis.R"
    target.write_bytes(b"alpha <- 1\n")
    patch = """*** Begin Patch
*** Update File: code/analysis.R
@@
-alpha <- 1
+alpha <- 2
\\ No newline at end of file
*** End Patch"""

    apply_transaction(project, [{"kind": "patch", "patch": patch}], origin="golden")
    assert target.read_bytes() == b"alpha <- 2"
