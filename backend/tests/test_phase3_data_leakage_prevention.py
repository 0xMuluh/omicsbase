"""Phase 3 Test Suite: Data Leakage & PII Prevention.

Verifies that sensitive tokens, secrets, credentials, and massive payloads are sanitized
before transmission to external LLM providers.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.services.sanitizer import sanitize_text, sanitize_messages, truncate_large_content
from app.services import llm


def test_sanitize_api_keys():
    raw_text = (
        "Here is the key: sk-proj-1234567890abcdef1234567890abcdef and "
        "Anthropic key sk-ant-api03-abcdef1234567890abcdef and "
        "Authorization: Bearer secret-token-xyz123."
    )
    clean = sanitize_text(raw_text)
    assert "sk-proj-" not in clean
    assert "sk-ant-api03-" not in clean
    assert "secret-token-xyz123" not in clean
    assert "[REDACTED_SECRET]" in clean


def test_sanitize_env_secrets():
    raw_text = (
        "Config values:\n"
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
        "OPENAI_API_KEY=sk-1234567890abcdef1234567890abcdef\n"
        "DATABASE_URL=postgres://admin:supersecretpass@localhost:5432/omicsdb"
    )
    clean = sanitize_text(raw_text)
    assert "wJalrXUtnFEMI" not in clean
    assert "supersecretpass" not in clean
    assert "[REDACTED_SECRET]" in clean


def test_truncate_large_content():
    huge_text = "A" * 100_000
    truncated = truncate_large_content(huge_text, max_chars=1_000)
    assert len(truncated) < 100_000
    assert "[TRUNCATED" in truncated


def test_biological_data_preserved():
    bio_text = (
        "Differential abundance analysis results:\n"
        "Taxon: Bacteroides_fragilis, Log2FC: 2.45, p_val: 0.0012, ASV_ID: ASV_98231.\n"
        "Gene: BRCA1, expression: 14.2 RPKM."
    )
    clean = sanitize_text(bio_text)
    assert clean == bio_text


@pytest.mark.asyncio
async def test_llm_gateway_sanitization(monkeypatch):
    recorded_prompts = []

    async def mock_call_openai(system_prompt, user_prompt, response_format, max_tokens, provider="openai"):
        recorded_prompts.append((system_prompt, user_prompt))
        return "Sanitized response"

    monkeypatch.setattr(llm, "_call_openai", mock_call_openai)
    monkeypatch.setattr(llm.settings, "llm_provider", "openai")

    system_input = "System prompt with key: sk-proj-1234567890abcdef1234567890abcdef"
    user_input = "User prompt with secret AWS_SECRET_ACCESS_KEY=secretval123"

    await llm.call_llm(system_input, user_input)

    assert len(recorded_prompts) == 1
    sys_sent, user_sent = recorded_prompts[0]

    assert "sk-proj-" not in sys_sent
    assert "secretval123" not in user_sent
    assert "[REDACTED_SECRET]" in sys_sent
    assert "[REDACTED_SECRET]" in user_sent


def test_preview_rows_canary_withheld_from_llm():
    """Constitution Phase 3: participant-level cell values must NOT reach LLM.

    Injects a canary string into preview_rows and asserts it does NOT
    appear in the formatted output sent to the planner/generator.
    """
    from app.services.file_inspector import format_file_summary_for_llm

    canary = "PARTICIPANT_CANARY_9f3c2a1b"
    summary = {
        "name": "metadata.csv",
        "format": "csv",
        "dimensions": {"rows": 100, "columns": 5},
        "columns": ["patient_id", "age", "condition", "site", "bmi"],
        "column_types": {
            "patient_id": "object",
            "age": "int64",
            "condition": "object",
            "site": "object",
            "bmi": "float64",
        },
        "preview_rows": [
            [canary, "34", "Treatment", "Helsinki", "24.5"],
            ["P002", "29", "Control", "Turku", "22.1"],
            ["P003", "41", "Treatment", "Oulu", "27.8"],
        ],
    }
    formatted = format_file_summary_for_llm(summary)
    assert canary not in formatted, (
        f"PHASE 3 FAILURE: Participant canary '{canary}' leaked into LLM-formatted output:\n{formatted}"
    )
    # Schema info should still be present
    assert "patient_id" in formatted
    assert "100 rows" in formatted or "100" in formatted

