"""Tests for the workspace executor and tool policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workspace_agent
from app.services.provider_errors import LLMQuotaError


def _project(tmp_path):
    project_dir = tmp_path / "project"
    results_dir = project_dir / "output" / "results"
    results_dir.mkdir(parents=True)
    (project_dir / "code").mkdir()
    (project_dir / "code" / "index.qmd").write_text("# Report\n")
    (results_dir / "alpha_diversity.csv").write_text(
        "sample_id,shannon,group\nS1,2.1,Control\nS2,3.2,Treatment\n"
    )
    return SimpleNamespace(
        id="project-1",
        name="Agent test",
        question="Compare groups",
        notes=None,
        status="completed",
        agent_state="completed",
        agent_memory={"summary": "Report ready"},
        agent_actions=[],
        study_manifest={"status": "ready", "domain": "microbiome"},
        analysis_plan={
            "project_name": "Agent test",
            "study_type": "two_group_comparison",
            "question": "Compare groups",
            "grouping_variable": "group",
            "group_levels": ["Control", "Treatment"],
            "workflow": [],
        },
        project_dir=str(project_dir),
        files=[],
    )


def test_build_mode_uses_structured_action_permission(tmp_path):
    request = SimpleNamespace(
        message="Why did this generation fail?",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="build",
    )
    executor = workspace_agent.WorkspaceAgentExecutor(
        project=_project(tmp_path),
        request=request,
        persisted_messages=[],
    )

    advertised = {tool["function"]["name"] for tool in executor.tools}
    assert executor.mutations_allowed is True
    assert "set_plan" in advertised
    assert "render_report" in advertised
    assert "stage_report_pack" in advertised
    assert "run_r_script" in advertised


@pytest.mark.asyncio
async def test_discuss_mode_is_read_only_regardless_of_message(tmp_path):
    request = SimpleNamespace(
        message="Run the analysis now",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="discuss",
    )
    executor = workspace_agent.WorkspaceAgentExecutor(
        project=_project(tmp_path),
        request=request,
        persisted_messages=[],
    )

    advertised = {tool["function"]["name"] for tool in executor.tools}
    assert executor.mutations_allowed is False
    assert "set_plan" not in advertised
    assert "render_report" not in advertised
    assert "stage_report_pack" not in advertised
    result = await executor.execute_tool(
        "set_analysis_variables",
        {"grouping_variable": "group"},
        step=1,
        tool_call_id="blocked-config",
        persisted_arguments={},
        step_text="",
    )
    assert result.observation["status"] == "error"
    assert "Discuss mode" in result.observation["error"]
    assert result.end_turn is False


@pytest.mark.asyncio
async def test_render_report_runs_inline_and_keeps_the_turn_alive(tmp_path):
    request = SimpleNamespace(
        message="Render the report",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="build",
    )

    async def fake_render_handler(arguments):
        return {
            "status": "completed",
            "render_status": "completed",
            "pages": ["index.html"],
            "errors": [],
        }

    executor = workspace_agent.WorkspaceAgentExecutor(
        project=_project(tmp_path),
        request=request,
        persisted_messages=[],
        render_handler=fake_render_handler,
    )
    result = await executor.execute_tool(
        "render_report",
        {},
        step=1,
        tool_call_id="inline-render",
        persisted_arguments={},
        step_text="Rendering",
    )
    assert result.end_turn is False
    assert result.observation["render_status"] == "completed"
    assert any(
        event.get("type") == "action_event" and event["event"].get("status") == "ok"
        for event in result.events
    )


def test_capability_routing_hides_legacy_recipe_and_repair_alias(tmp_path):
    request = SimpleNamespace(
        message="Review the current workspace",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="build",
    )
    executor = workspace_agent.WorkspaceAgentExecutor(
        project=_project(tmp_path),
        request=request,
        persisted_messages=[],
    )
    advertised = {tool["function"]["name"] for tool in executor.tools}
    assert "repair_report" not in advertised
    assert "list_recipes" not in advertised

    (tmp_path / "project" / "code" / "study_config.yml").write_text("analyses: {}\n")
    recipe_project = executor.project
    recipe_executor = workspace_agent.WorkspaceAgentExecutor(
        project=recipe_project,
        request=request,
        persisted_messages=[],
    )
    recipe_advertised = {tool["function"]["name"] for tool in recipe_executor.tools}
    assert "list_recipes" in recipe_advertised


def test_typed_provider_failure_is_terminal_even_for_edit_request(tmp_path):
    request = SimpleNamespace(
        message="Fix the report caption",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="build",
    )
    executor = workspace_agent.WorkspaceAgentExecutor(
        project=_project(tmp_path),
        request=request,
        persisted_messages=[],
    )
    events = executor.fallback_events(
        LLMQuotaError("qwen", "Provider quota exhausted; explicitly retry later.")
    )

    assert [event["type"] for event in events] == ["token", "final"]
    assert all(event["type"] != "action" for event in events)
    assert "quota exhausted" in events[-1]["message"]
