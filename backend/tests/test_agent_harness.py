from scripts.agent_harness import run_harness


def test_deterministic_harness_is_provider_independent():
    result = run_harness()

    assert result["harness"] == "omicsbase-agent-harness-v1"
    assert result["routing"]["accuracy"] == 1.0
    assert result["routing"]["judge_handoff_cases"] == 1
    assert result["budget"]["checks_passed"] is True
    assert result["bounded_json_valid"] is True
    assert all(
        details["present"]
        for lens in result["tool_contracts"].values()
        for details in lens.values()
    )


def test_deterministic_harness_repeats_exactly():
    assert run_harness() == run_harness()
