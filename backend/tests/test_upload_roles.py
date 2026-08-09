"""Tests for upload role handling (agent-assigned; no heuristics)."""

from __future__ import annotations

from app.services import data_acquisition, document_classify


def test_no_heuristic_role_functions_remain():
    """Keyword/rule-based role guessing must not exist anywhere."""
    from app.api import projects_files as pf

    assert not hasattr(pf, "_guess_file_role")
    assert not hasattr(data_acquisition, "_guess_role")
    assert not hasattr(document_classify, "_rule_preclassify")


def test_classifier_payload_contains_no_rule_hints(monkeypatch):
    captured = {}

    async def fake_call_llm(**kwargs):
        captured["user_prompt"] = kwargs.get("user_prompt", "")
        return '{"files": []}'

    monkeypatch.setattr(document_classify, "call_llm", fake_call_llm)
    import asyncio

    asyncio.run(
        document_classify.classify_uploads(
            [{"name": "plan.docx", "size_bytes": 10, "text": "plan"}, {"name": "counts.csv", "size_bytes": 10, "text": "Header: taxon,S1"}]
        )
    )
    assert "role_hint" not in captured["user_prompt"]
