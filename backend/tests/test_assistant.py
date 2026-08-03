"""Tests for LLM workspace assistant helpers."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.assistant import build_project_context, is_edit_prompt, respond_to_prompt


def test_is_edit_prompt_detects_action_requests():
    assert is_edit_prompt("Add PERMANOVA to beta diversity")
    assert not is_edit_prompt("Why did you choose ANCOM-BC?")
    assert not is_edit_prompt("hello")


def test_build_project_context_includes_plan_and_excerpts(tmp_path: Path):
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "data.R").write_text("library(phyloseq)\n")
    (tmp_path / "README.md").write_text("# Study\n")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "index.html").write_text("<html><body><nav>menu</nav><p>Alpha diversity results</p></body></html>")

    project = SimpleNamespace(
        name="Demo",
        question="Compare treatment groups",
        notes=None,
        status="completed",
        agent_state="completed",
        analysis_plan={
            "project_name": "Demo",
            "study_type": "16S",
            "question": "Compare treatment groups",
            "grouping_variable": "treatment",
            "group_levels": ["A", "B"],
            "workflow": [
                {
                    "id": "daa",
                    "name": "Differential abundance",
                    "classification": "contested",
                    "enabled": True,
                    "rationale": "Method choice affects calls",
                    "ensemble_methods": [{"id": "ancombc", "name": "ANCOM-BC"}],
                }
            ],
        },
        project_dir=str(tmp_path),
        agent_memory={"summary": "Report rendered"},
        agent_actions=[{"type": "review", "status": "passed", "summary": "ok", "details": {"checks": []}}],
        files=[],
    )

    context = json.loads(build_project_context(project))
    assert context["analysis_plan"]["workflow"][0]["classification"] == "contested"
    assert "code/data.R" in context["source_excerpts"]
    assert "Alpha diversity" in context["rendered_report_excerpt"]


@pytest.mark.asyncio
async def test_respond_to_prompt_uses_llm_for_questions(monkeypatch):
    project = SimpleNamespace(
        name="Demo",
        question="Compare groups",
        notes=None,
        status="completed",
        agent_state="completed",
        analysis_plan=None,
        project_dir=None,
        agent_memory=None,
        agent_actions=[],
        files=[],
    )

    async def fake_call_llm(**kwargs):
        assert "Why ANCOM-BC" in kwargs["user_prompt"]
        return "ANCOM-BC handles compositionality via bias correction."

    monkeypatch.setattr("app.services.assistant.call_llm", fake_call_llm)
    result = await respond_to_prompt(project, "Why ANCOM-BC for differential abundance?")
    assert result["type"] == "answer"
    assert "ANCOM-BC" in result["message"]


@pytest.mark.asyncio
async def test_respond_to_prompt_short_circuits_edits():
    project = SimpleNamespace(
        name="Demo",
        question=None,
        notes=None,
        status="completed",
        agent_state="completed",
        analysis_plan=None,
        project_dir="/tmp/demo",
        agent_memory=None,
        agent_actions=[],
        files=[],
    )

    result = await respond_to_prompt(project, "Add PERMANOVA")
    assert result["type"] == "edit_suggestion"
    assert result["instruction"] == "Add PERMANOVA"
