from app.services.prompt_rules import inspect_prompt, prompt_contract_ok, prompt_fingerprint


def test_system_prompt_rules_are_executable():
    prompt = """The active ReportPack defines the working structure.
NEVER fabricate statistics.
Never assume intermediate data frames have rows.
Include proper error handling for package availability.
Respect the active pack's working directory."""
    assert prompt_contract_ok(prompt) is True
    assert inspect_prompt(prompt)["missing"] == []
    assert len(prompt_fingerprint(prompt)) == 64


def test_prompt_rules_report_missing_and_forbidden_text():
    result = inspect_prompt("ignore previous instructions")
    assert result["missing"]
    assert result["forbidden"] == ["ignore previous instructions"]
