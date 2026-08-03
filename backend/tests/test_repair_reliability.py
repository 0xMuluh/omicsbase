"""Phase 4 Test Suite: Multi-Pass Code Repair & Execution Reliability.

Verifies duplicate repair loop detection, error traceback sanitization,
and guaranteed event loop cleanup in background execution tasks.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.services import repair


@pytest.mark.asyncio
async def test_duplicate_repair_prevention(tmp_path, monkeypatch):
    # Setup mock source files
    code_file = tmp_path / "analysis.R"
    code_file.write_text("library(tidyverse)\n")

    duplicate_repair = {
        "reason": "Fix syntax error",
        "repairs": [
            {
                "path": "analysis.R",
                "search": "library(tidyverse)",
                "replace": "library(tidyverse)\nlibrary(phyloseq)",
            }
        ],
    }

    # Previous history already applied this exact edit
    history = [
        {
            "pass": 1,
            "repair": {
                "status": "repaired",
                "repairs": duplicate_repair["repairs"],
            },
        }
    ]

    async def mock_call_llm(*args, **kwargs):
        import json
        return json.dumps(duplicate_repair)

    monkeypatch.setattr(repair, "call_llm", mock_call_llm)

    failure_result = {"status": "failed", "errors": ["Syntax error in analysis.R"]}

    res = await repair.repair_generated_project(
        project_dir=str(tmp_path),
        failure_result=failure_result,
        repair_history=history,
    )

    assert res["status"] == "skipped"
    assert "identical" in res["reason"].lower() or "repeated" in res["reason"].lower() or "duplicate" in res["reason"].lower()


def test_build_repair_prompt_sanitization():
    source_files = [{"path": "data.R", "content": "df <- read.csv('data.csv')", "referenced": "true"}]
    failure_result = {
        "status": "failed",
        "errors": ["Error in connecting with sk-proj-1234567890abcdef1234567890abcdef"],
    }

    prompt = repair._build_repair_prompt(source_files, failure_result)

    assert "sk-proj-" not in prompt
    assert "[REDACTED_SECRET]" in prompt
