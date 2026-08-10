from types import SimpleNamespace

from app.services.agent_plans import (
    DEAD_LETTER,
    DONE,
    READY,
    WAITING,
    append_continuation_step,
    attach_continuation_plan,
    build_continuation_plan,
    continuation_is_ready,
    continuation_prompt,
    get_continuation_plan,
    mark_continuation_consumed,
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
        arguments={"resume_from_checkpoint": True},
        dependency_status="completed",
    )
    plan["dependency_result"] = {"status": "completed", "error": None}
    attach_continuation_plan(run, plan)

    assert plan["status"] == READY
    assert continuation_is_ready(run) is True
    prompt = continuation_prompt(get_continuation_plan(run))
    assert "Do not enqueue the same action again" in prompt
    assert "job-1" in prompt
    assert "Original unresolved goal: Run the analysis" in prompt
    assert "resume_from_checkpoint" in prompt


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


def test_continuation_attempt_ceiling_dead_letters_plan():
    run = _run()
    plan = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Finish the report after the analysis",
        dependency_status="completed",
    )
    plan["max_attempts"] = 1
    attach_continuation_plan(run, plan)

    first = mark_continuation_running(run)
    assert first["attempts"] == 1
    first["status"] = READY
    attach_continuation_plan(run, first)

    assert mark_continuation_running(run) is None
    stored = get_continuation_plan(run)
    assert stored["status"] == DEAD_LETTER
    assert stored["requires_user"] is True
    assert run.resumable is False


def test_appending_a_step_preserves_previous_progress_and_prompt():
    run = _run()
    first = build_continuation_plan(
        run,
        action="run_analysis",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Run the first analysis",
        dependency_status="completed",
    )
    attach_continuation_plan(run, first)
    assert mark_continuation_running(run)["status"] == "running"

    second = append_continuation_step(
        run,
        action="write_report",
        dependency_kind="job",
        dependency_id="job-2",
        instruction="Write the report after the analysis",
        arguments={"format": "html"},
    )

    assert second["status"] == WAITING
    assert second["active_step_id"] == "step-2"
    assert second["steps"][0]["status"] == DONE
    assert second["steps"][1]["depends_on"] == ["step-1"]
    assert "Step 2 of 2" in continuation_prompt(second)
    assert "write_report" in continuation_prompt(second)


def test_dag_steps_activate_in_deterministic_topological_order():
    run = _run()
    plan = build_continuation_plan(
        run,
        action="prepare",
        dependency_kind="job",
        dependency_id="job-1",
        instruction="Prepare the input",
        steps=[
            {
                "id": "step-1",
                "action": "prepare",
                "dependency_kind": "job",
                "dependency_id": "job-1",
                "instruction": "Prepare the input",
                "dependency_status": "completed",
                "status": READY,
            },
            {
                "id": "step-2",
                "action": "report",
                "instruction": "Create the report",
                "dependency_status": "completed",
                "depends_on": ["step-1"],
            },
            {
                "id": "step-3",
                "action": "archive",
                "instruction": "Archive the report",
                "dependency_status": "completed",
                "depends_on": ["step-1"],
            },
        ],
    )
    attach_continuation_plan(run, plan)

    first_consumed = mark_continuation_consumed(run)
    assert first_consumed["status"] == READY
    assert first_consumed["active_step_id"] == "step-2"
    assert first_consumed["steps"][1]["status"] == READY
    assert first_consumed["steps"][2]["status"] == READY

    second_consumed = mark_continuation_consumed(run)
    assert second_consumed["status"] == READY
    assert second_consumed["active_step_id"] == "step-3"
    assert second_consumed["completed_steps"] == ["step-1", "step-2"]
