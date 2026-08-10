from scripts.agent_harness import run_harness


def test_deterministic_harness_is_provider_independent():
    result = run_harness()

    assert result["harness"] == "omicsbase-agent-harness-v4"
    assert result["routing"]["accuracy"] == 1.0
    assert result["routing"]["judge_handoff_cases"] == 4
    assert result["budget"]["checks_passed"] is True
    assert result["bounded_json_valid"] is True
    assert result["native_eval"]["production_loop"] == "app.services.agent_core.run_agent_loop"
    assert result["native_eval"]["success_rate"] == 1.0
    assert result["native_eval"]["wait_transitions"] == 3
    assert result["native_eval"]["continuation_goal_retained"] is True
    assert result["native_eval"]["total"] == 54
    assert result["native_eval"]["observations_forwarded"] == 52
    assert result["native_eval"]["observations_retained"] == 54
    assert all(
        details["present"]
        for lens in result["tool_contracts"].values()
        for details in lens.values()
    )


def test_deterministic_harness_repeats_exactly():
    assert run_harness() == run_harness()
