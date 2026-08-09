from __future__ import annotations

import hashlib

import pytest

from app.services.edit_engine import EditConflict
from app.services.edit_review import approve_edit_review, prepare_edit_review, read_edit_review


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_review_prepare_is_non_mutating_and_approval_commits(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\n")

    proposal = prepare_edit_review(
        tmp_path,
        [{"path": "code/analysis.R", "kind": "replace", "search": "alpha <- 1", "replace": "alpha <- 2"}],
        summary="Review scientific edit",
    )

    assert proposal["status"] == "pending"
    assert proposal["prepared"]["files"][0]["diff"]
    assert target.read_text() == "alpha <- 1\n"

    committed = approve_edit_review(tmp_path, proposal["review_id"])
    assert committed.status == "committed"
    assert target.read_text() == "alpha <- 2\n"
    assert read_edit_review(tmp_path, proposal["review_id"])["status"] == "committed"


def test_review_approval_rejects_stale_source_without_overwrite(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\n")

    proposal = prepare_edit_review(
        tmp_path,
        [{"path": "code/analysis.R", "kind": "replace", "search": "alpha <- 1", "replace": "alpha <- 2"}],
    )
    target.write_text("alpha <- 9\n")

    with pytest.raises(EditConflict):
        approve_edit_review(tmp_path, proposal["review_id"])
    assert target.read_text() == "alpha <- 9\n"
    assert read_edit_review(tmp_path, proposal["review_id"])["status"] == "pending"
