"""Tests for the persistent tool-using workspace agent loop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workspace_agent
from app.services.provider_errors import LLMQuotaError


def _pin_judge_to_tools(monkeypatch):
    """These legacy-path tests expect the tool loop; pin the semantic judge
    so it never hits a live provider."""

    async def judge_needs_tools(message):
        return "needs_tools"

    monkeypatch.setattr("app.services.intent_fastpath.classify_intent", judge_needs_tools)


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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Why did this generation fail?", False),
        ("Analyze why this generation failed", False),
        ("Inspect the failed job and explain it", False),
        ("What happens if I rerun the analysis?", False),
        ("Can you rerun the analysis?", True),
        ("Fix this generate failure", True),
        ("Complete the analysis report", True),
        ("For alpha diversity use only Shannon", True),
        ("Diagnose the failure and then fix it", True),
        ("Analyze why this failed and then fix it", True),
        ("Show me the failure logs", False),
        ("Show me an example dataset", True),
    ],
)
def test_explicit_workspace_mutation_intent(message, expected):
    assert workspace_agent.has_explicit_workspace_mutation_intent(message) is expected


@pytest.mark.asyncio
async def test_diagnostic_turn_cannot_invoke_pipeline_mutation(tmp_path):
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
    assert "inspect_failures" in advertised
    assert "plan_analysis" not in advertised
    assert "run_analysis" not in advertised

    for action in ("plan_analysis", "run_analysis"):
        result = await executor.execute_tool(
            action,
            {},
            step=1,
            tool_call_id=f"blocked-{action}",
            persisted_arguments={},
            step_text="",
        )
        assert result.observation["status"] == "error"
        assert "did not explicitly" in result.observation["error"]
        assert result.end_turn is False


@pytest.mark.asyncio
async def test_explicit_fix_turn_can_request_pipeline_action(tmp_path):
    request = SimpleNamespace(
        message="Fix this generate failure and rerun the analysis",
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
    assert "plan_analysis" not in advertised
    assert "run_analysis" in advertised
    result = await executor.execute_tool(
        "run_analysis",
        {},
        step=1,
        tool_call_id="allowed-run",
        persisted_arguments={},
        step_text="Resuming generation",
    )
    assert result.end_turn is True
    assert result.final_event["mutation_authorized"] is True


@pytest.mark.asyncio
async def test_explicit_replan_turn_can_request_planning(tmp_path):
    request = SimpleNamespace(
        message="Replan the analysis from the study inputs",
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
    assert "plan_analysis" in advertised
    result = await executor.execute_tool(
        "plan_analysis",
        {},
        step=1,
        tool_call_id="allowed-plan",
        persisted_arguments={},
        step_text="Replanning the analysis",
    )
    assert result.end_turn is True
    assert result.final_event["mutation_authorized"] is True


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


@pytest.mark.asyncio
async def test_agent_inspects_result_table_before_answering(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tool","tool":"read_results","arguments":{"path":"output/results/alpha_diversity.csv"},"reason":"Read the requested result table"}',
            '{"type":"final","message":"Treatment has the higher observed Shannon value in this two-row table."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Which group has higher Shannon diversity?",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    types = [event["type"] for event in events]
    assert types[0] == "status"
    assert "tool_started" in types
    assert "tool_completed" in types
    assert "action_event" in types
    assert events[-1]["type"] == "final"
    assert events[-1]["message"].startswith("Treatment")
    assert any(event["type"] == "token" for event in events)


@pytest.mark.asyncio
async def test_agent_falls_back_to_verified_edit_action(tmp_path, monkeypatch):
    async def unavailable_llm(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(workspace_agent, "call_llm", unavailable_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Add a caption to the alpha diversity figure",
        selected_file="code/index.qmd",
        selected_content="# Unsaved report\n",
        selected_content_dirty=True,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    assert events[-1]["type"] == "action"
    assert events[-1]["action"] == "edit_project"
    assert "rerender" in events[-1]["message"]


@pytest.mark.asyncio
async def test_agent_streams_final_message_tokens(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        return '{"type":"final","message":"Shannon is higher in treatment."}'

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Summarize Shannon",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert any(event["type"] == "token" for event in events)
    assert events[-1]["type"] == "final"
    assert events[-1]["message"] == "Shannon is higher in treatment."


@pytest.mark.asyncio
async def test_agent_uses_run_r_before_answering_package_question(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tool","tool":"run_r","arguments":{"code":"data.frame(a=1:2,b=c(\\"x\\",\\"y\\"))"},"reason":"Inspect example frame"}',
            '{"type":"final","message":"The frame has columns a and b."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    def fake_run_r(code, *, cwd=None, timeout_s=30):
        assert "data.frame" in code
        return {
            "status": "ok",
            "stdout": "",
            "stderr": "",
            "summary": {"class": "data.frame", "rows": 2, "columns": 2, "colnames": ["a", "b"], "head": ["1 | x", "2 | y"]},
        }

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    monkeypatch.setattr("app.services.r_inspect.run_r_inspect", fake_run_r)
    _pin_judge_to_tools(monkeypatch)

    request = SimpleNamespace(
        message="What colnames does that package dataset have?",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    tool_started = next(event for event in events if event["type"] == "tool_started")
    tool_completed = next(event for event in events if event["type"] == "tool_completed")
    assert tool_started["tool"] == "run_r"
    assert tool_started["reason"] == "Running R inspection"
    assert "2×2" in tool_completed["summary"]
    assert events[-1]["type"] == "final"
    assert "a and b" in events[-1]["message"]


@pytest.mark.asyncio
async def test_agent_asks_user_and_pauses_turn(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        return (
            '{"type":"tool","tool":"ask_user","arguments":'
            '{"question":"Which groups should be compared?","options":["Control vs Disease","Male vs Female"]},'
            '"reason":"Grouping cannot be inferred from the manifest"}'
        )

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Compare the groups in my study",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )

    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    question_events = [event for event in events if event["type"] == "question"]
    assert len(question_events) == 1
    pending = question_events[0]["question"]
    assert pending["question"] == "Which groups should be compared?"
    assert pending["options"] == ["Control vs Disease", "Male vs Female"]

    assert events[-1]["type"] == "final"
    assert events[-1]["awaiting_answer"]["id"] == pending["id"]
    assert events[-1]["awaiting_answer"]["question"] == pending["question"]


@pytest.mark.asyncio
async def test_ask_user_without_question_feeds_back_error(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        return '{"type":"tool","tool":"ask_user","arguments":{},"reason":"oops"}'

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="test",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert not any(event["type"] == "question" for event in events)


@pytest.mark.asyncio
async def test_discuss_mode_blocks_mutation_actions(tmp_path, monkeypatch):
    async def fake_llm(**kwargs):
        assert "Discuss mode" in kwargs["system_prompt"] or "discuss" in kwargs["system_prompt"].lower()
        return (
            '{"type":"action","action":"edit_project","instruction":"change caption",'
            '"message":"I will edit the caption"}'
        )

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Change the alpha caption",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
        chat_mode="discuss",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert events[-1]["type"] == "final"
    assert "The Plan" in events[-1]["message"]
    assert events[-1]["quick_actions"][0]["type"] == "implement"
    assert not any(event["type"] == "action" for event in events)


@pytest.mark.asyncio
async def test_agent_greeting_is_model_handled(tmp_path, monkeypatch):
    """Greetings are not a hardcoded vocabulary: the deterministic gate hands
    them to the semantic judge and the model answers naturally."""
    import app.services.intent_fastpath as intent_fastpath

    async def fake_judge(self, message):
        return "conceptual"

    async def fake_stream_simple_answer(message, knowledge_context=None, **kwargs):
        yield {"type": "status", "status": "thinking", "message": "Answering directly", "fast": True}
        yield {"type": "token", "token": "Hi! "}
        yield {"type": "final", "message": "Hi! What would you like to work on?", "fast": True}

    monkeypatch.setattr(workspace_agent.WorkspaceAgentExecutor, "judge_intent", fake_judge)
    monkeypatch.setattr(intent_fastpath, "stream_simple_answer", fake_stream_simple_answer)
    request = SimpleNamespace(
        message="hello",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path=None,
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]
    assert events[-1]["type"] == "final"
    assert "ready to assist" not in events[-1]["message"]
    assert "Hi!" in events[-1]["message"]


@pytest.mark.asyncio
async def test_agent_multi_tool_batching(tmp_path, monkeypatch):
    decisions = iter(
        [
            '{"type":"tools","tools":[{"tool":"read_file","arguments":{"path":"code/index.qmd"}},{"tool":"read_results","arguments":{"path":"output/results/alpha_diversity.csv"}}],"reason":"Inspect both report and result table"}',
            '{"type":"final","message":"Both files inspected."}',
        ]
    )

    async def fake_llm(**kwargs):
        return next(decisions)

    monkeypatch.setattr(workspace_agent, "call_llm", fake_llm)
    _pin_judge_to_tools(monkeypatch)
    request = SimpleNamespace(
        message="Inspect report and results",
        selected_file=None,
        selected_content=None,
        selected_content_dirty=False,
        preview_path="index.html",
    )
    events = [
        event
        async for event in workspace_agent.stream_workspace_agent(
            _project(tmp_path),
            request,
            persisted_messages=[],
        )
    ]

    completed_tools = [e["tool"] for e in events if e["type"] == "tool_completed"]
    assert completed_tools == ["read_file", "read_results"]
    assert events[-1]["type"] == "final"
    assert events[-1]["message"] == "Both files inspected."
