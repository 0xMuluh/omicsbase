"""Executable golden-study coverage for the ReportPack runtime contract."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from app.services.capability_contract import (
    load_capability_contract,
    validate_capability_bindings,
    write_capability_contract,
)
from app.services.execution_contract import load_execution_contract, write_execution_contract
from app.services.report_pack import load_report_pack
from app.services import runner
from app.services.reviewer import review_render_output


FIXTURE = Path(__file__).parent / "fixtures" / "golden_study"
R_AVAILABLE = shutil.which("Rscript") is not None
QUARTO_AVAILABLE = shutil.which("quarto") is not None


@pytest.mark.skipif(
    not (R_AVAILABLE and QUARTO_AVAILABLE),
    reason="golden-study execution requires Rscript and Quarto",
)
@pytest.mark.asyncio
async def test_golden_study_executes_declared_steps_and_validator_evidence(tmp_path: Path, monkeypatch):
    # The CI fixture is dependency-light and runs directly only in this test;
    # deployed projects still require the configured Docker sandbox.
    from app.config import settings
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "use_docker_sandbox", False)
    project = tmp_path / "golden-study"
    shutil.copytree(FIXTURE, project)
    pack = load_report_pack(project, domain="test")

    execution_contract_path = write_execution_contract(project, pack)
    capability_contract_path = write_capability_contract(
        project,
        pack,
        {"capabilities": ["golden-study"], "parameters": {"grouping_variable": "group"}},
    )
    assert execution_contract_path and capability_contract_path
    assert load_execution_contract(project) is not None
    contract = load_capability_contract(project)
    validation = validate_capability_bindings(project, contract, run_r_parse=True)
    assert validation.valid, [issue.as_dict() for issue in validation.issues]

    result = await runner.run_project(str(project))
    assert result["status"] == "completed", result

    with (project / "output" / "results.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {"group": "control", "value": "11"},
        {"group": "treatment", "value": "21"},
    ]
    report = project / "output" / "report.html"
    assert report.is_file() and report.stat().st_size > 0

    provenance = result["provenance"]
    assert [item["step_id"] for item in provenance["validators"]] == ["validate-study"]
    assert provenance["validators"][0]["status"] == "completed"
    assert provenance["validators"][0]["input_sha256"]
    artifact = next(item for item in provenance["artifacts"] if item["path"] == "output/report.html")
    assert artifact["exists"] is True
    review = review_render_output(project)
    validator_check = next(item for item in review["checks"] if item["name"] == "validator_provenance")
    assert validator_check["status"] == "passed", review


@pytest.mark.skipif(
    not (R_AVAILABLE and QUARTO_AVAILABLE),
    reason="golden-study execution requires Rscript and Quarto",
)
@pytest.mark.asyncio
async def test_golden_study_validator_blocks_report_on_bad_input(tmp_path: Path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "use_docker_sandbox", False)
    project = tmp_path / "golden-study"
    shutil.copytree(FIXTURE, project)
    # The validator's expected group means are deliberate: this mutation must
    # stop the entrypoint from publishing a report artifact.
    (project / "data" / "study.csv").write_text(
        "sample_id,group,value\nS1,control,10\nS2,control,12\nS3,treatment,20\nS4,treatment,30\n",
        encoding="utf-8",
    )
    pack = load_report_pack(project, domain="test")
    write_execution_contract(project, pack)
    write_capability_contract(project, pack, {"capabilities": ["golden-study"]})

    result = await runner.run_project(str(project))
    assert result["status"] == "failed", result
    assert result["errors"][-1]["step"] == "validator"
    assert not (project / "output" / "report.html").exists()
    validator_record = next(
        item for item in result["provenance"]["validators"]
        if item["step_id"] == "validate-study"
    )
    assert validator_record["status"] == "failed"
