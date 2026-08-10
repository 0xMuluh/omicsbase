"""Phase 4 Test Suite: Multi-Pass Code Repair & Execution Reliability.

Verifies duplicate repair loop detection, error traceback sanitization,
and guaranteed event loop cleanup in background execution tasks.
"""

from __future__ import annotations

from pathlib import Path
import json
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


@pytest.mark.asyncio
async def test_automatic_repair_cannot_weaken_contract_validator(tmp_path, monkeypatch):
    code = tmp_path / "code"
    output = tmp_path / "output"
    code.mkdir()
    output.mkdir()
    analysis = code / "analysis.R"
    validator = code / "validate.R"
    main = code / "main.R"
    analysis.write_text("result <- data.frame(value = 0)\n")
    validator.write_text("stopifnot(result$value > 0)\n")
    main.write_text("quarto::quarto_render()\n")
    (tmp_path / "execution_contract.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "report_pack": {
                    "id": "assurance-test",
                    "version": "1",
                    "domain": "test",
                    "manifest_sha256": "a" * 64,
                    "source_tree_sha256": "b" * 64,
                },
                "working_directory": "code",
                "render": "entrypoint",
                "entrypoint": "code/main.R",
                "steps": [
                    {"id": "analyze", "path": "code/analysis.R", "role": "analysis"},
                    {"id": "validate", "path": "code/validate.R", "role": "validator"},
                ],
                "artifacts": ["output/index.html"],
            }
        )
    )
    proposed = {
        "reason": "Make the assertion pass",
        "repairs": [
            {
                "path": "code/validate.R",
                "search": "stopifnot(result$value > 0)",
                "replace": "stopifnot(TRUE)",
            }
        ],
    }

    async def mock_call_llm(*args, **kwargs):
        return json.dumps(proposed)

    monkeypatch.setattr(repair, "call_llm", mock_call_llm)

    result = await repair.repair_generated_project(
        str(tmp_path),
        {
            "status": "failed",
            "errors": [
                {"step": "validator", "file": "code/validate.R", "error": "assertion failed"}
            ],
        },
    )

    assert result["status"] == "skipped"
    assert validator.read_text() == "stopifnot(result$value > 0)\n"
    assert any(item["strategy"] == "protected" for item in result["apply_results"])


def test_line_targeted_repair_uses_runtime_range_and_hash(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\nbeta <- 2\ngamma <- 3\n")
    base_sha256 = repair.sha256_bytes(target.read_bytes())

    result = repair._apply_line_repairs(
        tmp_path,
        [
            {
                "path": "code/analysis.R",
                "line": 2,
                "diagnosis": "replace the stale beta assignment",
                "replacement": "beta <- 9",
                "base_sha256": base_sha256,
            }
        ],
    )

    assert all(item.ok for item in result)
    assert target.read_text() == "alpha <- 1\nbeta <- 9\ngamma <- 3\n"
    assert result[0].strategy == "line_replace"


def test_line_targeted_repair_rejects_stale_hash_without_writing(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    target = code / "analysis.R"
    target.write_text("alpha <- 1\nbeta <- 2\n")
    original = target.read_text()

    result = repair._apply_line_repairs(
        tmp_path,
        [
            {
                "path": "code/analysis.R",
                "line": 1,
                "replacement": "alpha <- 9",
                "base_sha256": "0" * 64,
            }
        ],
    )

    assert not any(item.ok for item in result)
    assert target.read_text() == original
    assert any(item.strategy == "conflict" for item in result)


@pytest.mark.asyncio
async def test_external_failure_is_routed_without_model_call(tmp_path, monkeypatch):
    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError("external failures must not call the repair model")

    monkeypatch.setattr(repair, "call_llm", unexpected_model_call)
    result = await repair.repair_generated_project(
        str(tmp_path),
        {"status": "failed", "errors": ["there is no package called 'vegan'"]},
    )

    assert result["status"] == "skipped"
    assert result["diagnosis"]["route"] == "dependency_policy"
