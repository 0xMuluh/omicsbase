from app.services.agent_failures import (
    classify_tool_failure,
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
