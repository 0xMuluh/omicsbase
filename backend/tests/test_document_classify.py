"""Tests for agentic, privacy-aware document classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import document_classify as dc


def test_extract_text_txt(tmp_path: Path):
    source = tmp_path / "plan.txt"
    source.write_text("Analysis plan: compare groups at two visits.")
    assert "compare groups" in dc.extract_document_text(str(source)).lower()


def test_extract_text_size_cap(tmp_path: Path):
    source = tmp_path / "huge.txt"
    source.write_text("x" * (dc.MAX_INSPECT_BYTES + 1))
    assert dc.extract_document_text(str(source)) == ""


def test_extract_text_unknown_binary(tmp_path: Path):
    source = tmp_path / "data.biom"
    source.write_bytes(b"\x00\x01\x02binary")
    assert dc.extract_document_text(str(source)) == ""


def test_extract_xlsx_headers_only(tmp_path: Path):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["sample_id", "condition", "age"])
    sheet.append(["S1", "case", 34])
    sheet.append(["S2", "control", 41])
    path = tmp_path / "meta.xlsx"
    workbook.save(path)
    text = dc.extract_document_text(str(path))
    assert "sample_id" in text
    assert "S1" not in text  # privacy: cell values never leak


def test_docx_stdlib_fallback(tmp_path: Path):
    import zipfile

    from xml.sax.saxutils import escape

    path = tmp_path / "plan.docx"
    document_xml = f"""<?xml version="1.0"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>{escape("Draft plan: two-group design")}</w:t></w:r></w:p></w:body>
    </w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    text = dc.extract_document_text(str(path))
    assert "Draft plan" in text


def test_classifier_payload_has_no_rule_hints(monkeypatch):
    captured = {}

    async def fake_call_llm(**kwargs):
        captured["user_prompt"] = kwargs.get("user_prompt", "")
        return '{"files": []}'

    monkeypatch.setattr(dc, "call_llm", fake_call_llm)
    import asyncio

    asyncio.run(dc.classify_uploads([{"name": "plan.docx", "size_bytes": 10, "text": "plan"}]))
    assert "role_hint" not in captured["user_prompt"]


def test_classify_uploads_parses_llm_json(monkeypatch):
    async def fake_call_llm(**kwargs):
        return '{"files": [{"file": "plan.docx", "role": "analysis_plan", "is_plan": true, "reason": "plan"}, {"file": "counts.xlsx", "role": "feature_table", "is_plan": false, "reason": "data"}]}'

    monkeypatch.setattr(dc, "call_llm", fake_call_llm)
    files = [
        {"name": "plan.docx", "size_bytes": 100, "text": "Draft plan text"},
        {"name": "counts.xlsx", "size_bytes": 200, "text": "Sheets: data\nSheet1 headers: taxon, S1"},
    ]
    import asyncio

    results = asyncio.run(dc.classify_uploads(files))
    assert results["plan.docx"]["role"] == "analysis_plan"
    assert results["plan.docx"]["is_plan"] is True
    assert results["counts.xlsx"]["role"] == "feature_table"


def test_classify_uploads_no_roles_assigned_on_llm_failure(monkeypatch):
    async def failing_call_llm(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(dc, "call_llm", failing_call_llm)
    files = [{"name": "clinical.csv", "size_bytes": 50, "text": "Header: sample_id,group"}]
    import asyncio

    results = asyncio.run(dc.classify_uploads(files))
    assert results == {}  # roles left unchanged — no heuristic fallback


def test_agentic_plan_sources_returns_plan_text(monkeypatch, tmp_path):
    import asyncio

    class FakeFile:
        def __init__(self, name, path, role):
            self.name = name
            self.file_path = str(path)
            self.file_role = role

    tmp = tmp_path / "classify_test"
    tmp.mkdir()
    plan_path = tmp / "plan.docx"
    import zipfile

    with zipfile.ZipFile(plan_path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>Plan: two-group design at visit 5</w:t></w:r></w:p></w:body></w:document>",
        )
    files = [FakeFile("plan.docx", plan_path, "other")]

    async def fake_classify(extracted):
        return {"plan.docx": {"role": "analysis_plan", "is_plan": True, "reason": "plan"}}

    monkeypatch.setattr(dc, "classify_uploads", fake_classify)
    sources = asyncio.run(dc.agentic_plan_sources(files))
    assert any("two-group design" in source for source in sources)
    assert files[0].file_role == "analysis_plan"  # reclassified
