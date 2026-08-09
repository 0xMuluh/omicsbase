from __future__ import annotations

import json

from app.services.context_budget import bounded_json
from app.services.workspace_agent import _workspace_live_context


def test_bounded_json_is_valid_and_keeps_priority_fields():
    value = {
        "analysis_plan": {"question": "x" * 5000, "workflow": list(range(30))},
        "study_manifest": {"status": "ready", "files": [{"name": str(i)} for i in range(100)]},
        "low_priority": "z" * 10_000,
    }

    rendered = bounded_json(
        value,
        1200,
        priority_keys=("analysis_plan", "study_manifest"),
    )

    assert len(rendered) <= 1200
    parsed = json.loads(rendered)
    assert "analysis_plan" in parsed
    assert "study_manifest" in parsed
    assert parsed["_context_truncated"] is True


def test_workspace_snapshot_remains_valid_json_when_compacted():
    context = _workspace_live_context({
        "analysis_plan": {"question": "q" * 20_000},
        "study_manifest": {"status": "ready", "files": [{"name": str(i)} for i in range(200)]},
        "low_priority": "z" * 20_000,
    })
    body = context.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    parsed = json.loads(body)
    assert parsed["_context_truncated"] is True
    assert "analysis_plan" in parsed
    assert "study_manifest" in parsed


def test_bounded_json_handles_non_finite_values_without_invalid_json():
    rendered = bounded_json({"value": float("nan"), "items": {1, 2}}, 200)

    assert json.loads(rendered)["value"] is None
