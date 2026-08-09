from __future__ import annotations

import json

import pytest

from app.services.edit_engine import EditOperation, apply_transaction, sha256_bytes
from app.services.edit_recovery import recover_edit_journals


def _mark_interrupted(project, transaction_id):
    manifest_path = project / ".omicsbase" / "edits" / transaction_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "committing"
    manifest_path.write_text(json.dumps(manifest))


def test_recovery_rolls_back_a_known_partial_commit(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    first = code / "one.R"
    second = code / "two.R"
    first.write_text("one <- 1\n")
    second.write_text("two <- 1\n")
    result = apply_transaction(
        tmp_path,
        [
            EditOperation(path="code/one.R", kind="rewrite", content="one <- 2\n", base_sha256=sha256_bytes(first.read_bytes())),
            EditOperation(path="code/two.R", kind="rewrite", content="two <- 2\n", base_sha256=sha256_bytes(second.read_bytes())),
        ],
        origin="test",
    )
    _mark_interrupted(tmp_path, result.transaction_id)
    first.write_text("one <- 1\n")  # before image; second remains after image

    recovered = recover_edit_journals(tmp_path)
    assert recovered[0]["status"] == "rolled_back"
    assert first.read_text() == "one <- 1\n"
    assert second.read_text() == "two <- 1\n"


def test_recovery_refuses_unknown_external_state(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("value <- 1\n")
    result = apply_transaction(
        tmp_path,
        [EditOperation(path="code/analysis.R", kind="rewrite", content="value <- 2\n", base_sha256=sha256_bytes(target.read_bytes()))],
        origin="test",
    )
    _mark_interrupted(tmp_path, result.transaction_id)
    target.write_text("value <- 99\n")

    recovered = recover_edit_journals(tmp_path)
    assert recovered[0]["status"] == "recovery_conflict"
    assert target.read_text() == "value <- 99\n"
