"""Tests for longer agent loops and inline acquisition continuation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workspace_agent


def _project(tmp_path):
    return SimpleNamespace(
        id="project-1",
        name="Agent test",
        question="Use GlobalPatterns",
        notes=None,
        status="created",
        agent_state="idle",
        agent_memory={"summary": "Awaiting data"},
        agent_actions=[],
        study_manifest={"status": "invalid", "domain": "unknown"},
        analysis_plan=None,
        project_dir=None,
        files=[],
    )


@pytest.mark.asyncio
async def test_inline_import_continues_then_queues_plan(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"action","action":"import_package_data","arguments":{"package":"phyloseq","dataset":"GlobalPatterns"},"message":"Importing GlobalPatterns"}',
            '{"type":"action","action":"plan_analysis","arguments":{},"message":"Planning after import"}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    monkeypatch.setattr(workspace_agent.settings, "agent_max_steps", 8)
    monkeypatch.setattr(workspace_agent.settings, "agent_allow_acquisition", True)

    async def judge_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_needs_tools)

    calls = []

    def inline_handler(action, arguments):
        calls.append((action, arguments))
        return {
            "status": "ok",
            "files": [{"name": "phyloseq_GlobalPatterns_feature_table.csv", "role": "feature_table"}],
            "study_manifest": {"status": "needs_input", "domain": "microbiome"},
        }

    request = SimpleNamespace(
        message="Import GlobalPatterns and plan the analysis",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path=None,
        chat_mode="build",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
            inline_action_handler=inline_handler,
        )
    ]

    assert calls and calls[0][0] == "import_package_data"
    assert any(event["type"] == "action" and event.get("action") == "plan_analysis" for event in events)
    # Inline step should not end the turn by itself.
    assert sum(1 for event in events if event["type"] == "action") == 1
