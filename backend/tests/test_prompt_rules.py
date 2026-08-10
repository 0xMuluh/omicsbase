from pathlib import Path

from app.services.prompt_rules import inspect_prompt, inspect_system_prompt, prompt_contract_ok, prompt_fingerprint


def test_system_prompt_rules_are_executable():
    prompt = """The active ReportPack defines the working structure.
NEVER fabricate statistics.
NEVER silently resolve a contested choice.
Include proper error handling for package availability.
Respect the active pack working directory.
Generated R and Quarto source is checked by the QA gate."""
    assert prompt_contract_ok(prompt) is True
    assert inspect_prompt(prompt)["missing"] == []
    assert len(prompt_fingerprint(prompt)) == 64


def test_prompt_rules_report_missing_and_forbidden_text():
    result = inspect_prompt("ignore previous instructions")
    assert result["missing"]
    assert result["forbidden"] == ["ignore previous instructions"]


def test_global_prompt_has_no_implementation_scars():
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "system.md"
    prompt = prompt_path.read_text()
    assert prompt_contract_ok(prompt) is True
    assert inspect_system_prompt(prompt)["forbidden"] == []
