from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.services import runner
from app.services.execution_provenance import (
    MAX_RUN_RECORDS,
    list_execution_provenance,
    write_execution_provenance,
)
from app.services.execution_contract import write_execution_contract
from app.services.report_pack import ReportPack, ReportPackExecution, ReportPackExecutionStep
from app.services.reviewer import review_render_output


def _project_with_validator_contract(base: Path) -> Path:
    (base / "code").mkdir(parents=True)
    (base / "output").mkdir(parents=True)
    (base / "code" / "analysis.R").write_text("message('analysis')\n")
    (base / "code" / "validate.R").write_text("stopifnot(TRUE)\n")
    (base / "code" / "report.qmd").write_text("---\ntitle: Report\n---\n# Report\n")
    (base / "code" / "_quarto.yml").write_text(
        "project:\n  type: website\n  output-dir: ../output\n  render:\n    - report.qmd\n"
    )
    pack = ReportPack(
        root=base,
        pack_id="provenance-pack",
        version="1.0.0",
        domain="test",
        name="Provenance pack",
        source="declared",
        manifest_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        execution=ReportPackExecution(
            working_directory="code",
            render="incremental",
            steps=(
                ReportPackExecutionStep("analysis", "code/analysis.R", "analysis"),
                ReportPackExecutionStep("validate", "code/validate.R", "validator"),
            ),
            artifacts=("output/index.html",),
        ),
    )
    write_execution_contract(base, pack)
    return base


@pytest.mark.asyncio
async def test_runner_persists_validator_provenance(tmp_path: Path, monkeypatch):
    base = _project_with_validator_contract(tmp_path)

    async def fake_run_command(cmd, cwd, **kwargs):
        if cmd[:2] == ["quarto", "render"]:
            (base / "output" / "index.html").write_text("<html>fresh report</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)
    result = await runner.run_project(str(base))

    assert result["status"] == "completed", result
    provenance = result["provenance"]
    assert provenance["status"] == "completed"
    assert provenance["run_id"]
    assert [item["step_id"] for item in provenance["validators"]] == ["validate"]
    assert provenance["validators"][0]["status"] == "completed"
    assert provenance["validators"][0]["input_sha256"]
    assert provenance["artifacts"][0]["exists"] is True
    stored = base / ".omicsbase" / "execution_runs" / f"{provenance['run_id']}.json"
    assert stored.is_file()
    assert json.loads(stored.read_text())["run_id"] == provenance["run_id"]
    assert list_execution_provenance(base)[0]["run_id"] == provenance["run_id"]
    review = review_render_output(base)
    validator_check = next(item for item in review["checks"] if item["name"] == "validator_provenance")
    assert validator_check["status"] == "passed"


def test_execution_provenance_retains_a_bounded_history(tmp_path: Path):
    for _ in range(MAX_RUN_RECORDS + 7):
        run_id = uuid.uuid4().hex
        write_execution_provenance(
            tmp_path,
            run_id=run_id,
            started_at="2026-01-01T00:00:00+00:00",
            result={"status": "completed", "errors": []},
            events=[],
        )

    records = list((tmp_path / ".omicsbase" / "execution_runs").glob("*.json"))
    assert len([path for path in records if path.name != "latest.json"]) <= MAX_RUN_RECORDS
    assert (tmp_path / ".omicsbase" / "execution_runs" / "latest.json").is_file()
