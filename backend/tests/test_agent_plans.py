from types import SimpleNamespace

from app.services.agent_plans import (
    READY,
    WAITING,
    attach_continuation_plan,
    build_continuation_plan,
    continuation_is_ready,
    continuation_prompt,
    get_continuation_plan,
    mark_continuation_running,
)


def _run():
    return SimpleNamespace(
        id="run-1",
        surface="workspace",
        project_id="project-1",
        note_thread_id=None,
        run_metadata={},
        resumable=False,
    )


def test_continuation_plan_is_bounded_and_resumable():
    run = _run()
    plan = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Run the analysis",
        arguments={"resume_from_checkpoint": True},
    )
    attach_continuation_plan(run, plan)

    stored = get_continuation_plan(run)
    assert stored["status"] == WAITING
    assert stored["dependency_id"] == "job-1"
    assert run.resumable is True
    assert continuation_is_ready(run) is False


def test_ready_plan_produces_a_non_repeating_resume_prompt():
    run = _run()
    plan = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Run the analysis",
        dependency_status="completed",
    )
    plan["dependency_result"] = {"status": "completed", "error": None}
    attach_continuation_plan(run, plan)

    assert plan["status"] == READY
    assert continuation_is_ready(run) is True
    prompt = continuation_prompt(get_continuation_plan(run))
    assert "Do not enqueue the same action again" in prompt
    assert "job-1" in prompt


def test_mark_continuation_running_increments_attempts():
    run = _run()
    plan = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Run the analysis",
        dependency_status="completed",
    )
    attach_continuation_plan(run, plan)

    running = mark_continuation_running(run)
    assert running["status"] == "running"
    assert running["attempts"] == 1
