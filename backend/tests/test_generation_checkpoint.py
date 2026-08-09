"""Focused tests for resumable, edit-safe source generation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services import generator
from app.services.generation_checkpoint import GenerationCheckpoint


def _checkpoint(base: Path, marker: str = "run-a") -> GenerationCheckpoint:
    return GenerationCheckpoint(
        base,
        run_inputs={
            "plan_sha256": marker,
            "study_manifest_sha256": "study",
            "uploads": [{"name": "study.csv", "sha256": "data"}],
            "report_pack": {"id": "pack", "version": "1", "source_tree_sha256": "tree"},
            "system_prompt_sha256": "prompt",
            "llm": {"provider": "test", "model": "test-model"},
        },
        generator_version="test-generator-v1",
    )


def test_matching_checkpoint_reuses_only_matching_output_hashes(tmp_path: Path):
    target = tmp_path / "code" / "page.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    checkpoint = _checkpoint(tmp_path)
    checkpoint.complete("qmd:code/page.qmd", ["code/page.qmd"])

    resumed = _checkpoint(tmp_path)
    decision = resumed.decide("qmd:code/page.qmd", ["code/page.qmd"])

    assert decision.action == "skip"
    state = json.loads((tmp_path / ".omicsbase" / "generation_checkpoint.json").read_text())
    assert state["run_fingerprint"] == resumed.run_fingerprint
    assert not list((tmp_path / ".omicsbase").glob("*.tmp"))


def test_resume_false_does_not_reuse_prior_unit_checkpoint(tmp_path: Path):
    target = tmp_path / "code" / "page.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    first = _checkpoint(tmp_path)
    first.complete("qmd:code/page.qmd", ["code/page.qmd"])

    fresh = GenerationCheckpoint(
        tmp_path,
        run_inputs=first.run_inputs,
        generator_version="test-generator-v1",
        resume=False,
    )
    assert fresh.decide("qmd:code/page.qmd", ["code/page.qmd"]).action == "preserve"
    assert fresh.state["units"] == {}


def test_divergent_completed_output_is_preserved_without_claiming_ownership(tmp_path: Path):
    target = tmp_path / "code" / "page.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    checkpoint = _checkpoint(tmp_path)
    checkpoint.complete("qmd:code/page.qmd", ["code/page.qmd"])
    target.write_text("user edit\n")

    resumed = _checkpoint(tmp_path)
    decision = resumed.decide("qmd:code/page.qmd", ["code/page.qmd"])
    assert decision.action == "preserve"
    resumed.preserve(
        "qmd:code/page.qmd",
        ["code/page.qmd"],
        reason=decision.reason,
    )

    assert target.read_text() == "user edit\n"
    owner = resumed.state["files"]["code/page.qmd"]
    assert owner["unit_id"] == "qmd:code/page.qmd"
    assert owner["sha256"] != resumed.state["units"]["qmd:code/page.qmd"]["outputs"]["code/page.qmd"]


def test_changed_fingerprint_reruns_owned_outputs_but_preserves_edits(tmp_path: Path):
    owned = tmp_path / "code" / "owned.qmd"
    edited = tmp_path / "code" / "edited.qmd"
    owned.parent.mkdir(parents=True)
    owned.write_text("old generated\n")
    edited.write_text("old generated\n")
    first = _checkpoint(tmp_path, "plan-a")
    first.complete("qmd:owned", ["code/owned.qmd"])
    first.complete("qmd:edited", ["code/edited.qmd"])
    edited.write_text("user edit\n")

    changed = _checkpoint(tmp_path, "plan-b")

    assert changed.decide("qmd:owned", ["code/owned.qmd"]).action == "run"
    assert changed.decide("qmd:edited", ["code/edited.qmd"]).action == "preserve"


def test_legacy_existing_files_are_preserved_while_missing_units_run(tmp_path: Path):
    legacy = tmp_path / "code" / "legacy.qmd"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("partial legacy workspace\n")
    checkpoint = _checkpoint(tmp_path)

    assert checkpoint.decide("qmd:legacy", ["code/legacy.qmd"]).action == "preserve"
    assert checkpoint.decide("qmd:missing", ["code/missing.qmd"]).action == "run"


@pytest.mark.asyncio
async def test_fail_fast_gather_cancels_unfinished_siblings():
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def blocked_sibling():
        sibling_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def failing_unit():
        await sibling_started.wait()
        raise RuntimeError("quota exhausted")

    with pytest.raises(RuntimeError, match="quota exhausted"):
        await generator._gather_fail_fast(blocked_sibling(), failing_unit())

    assert sibling_cancelled.is_set()
