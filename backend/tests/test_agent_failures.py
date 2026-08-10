from app.services.agent_failures import (
    classify_tool_failure,
    diagnose_repair_failure,
    is_retryable_failure,
)


def test_failure_classifier_distinguishes_repairable_and_external_failures():
    assert classify_tool_failure({"error": "parse error: unexpected symbol"}) == "syntax_error"
    assert classify_tool_failure({"error": "there is no package called 'vegan'"}) == "missing_package"
    assert classify_tool_failure({"error": "process timed out"}) == "timeout"
    assert classify_tool_failure({"error": "provider quota exhausted"}) == "infrastructure"
    assert classify_tool_failure({"error": "report validation failed"}) == "validator"
    assert is_retryable_failure("syntax_error") is True
    assert is_retryable_failure("missing_package") is False


def test_repair_diagnosis_extracts_line_window_and_external_route():
    syntax = diagnose_repair_failure(
        {
            "step": "qmd",
            "file": "code/page.qmd",
            "error": "parse error at code/page.qmd:91: unexpected token",
        }
    )
    assert syntax.repairable is True
    assert syntax.route == "targeted_source"
    assert {"path": "code/page.qmd", "line": 91} in syntax.file_references

    missing_file = diagnose_repair_failure(
        {"step": "qmd", "error": "QMD file is missing: code/missing.qmd"}
    )
    assert missing_file.repairable is False
    assert missing_file.route == "inspect_bindings"
