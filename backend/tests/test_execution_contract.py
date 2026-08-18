"""End-to-end and adversarial tests for ReportPack runtime contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services import runner
from app.services.execution_contract import (
    CONTRACT_NAME,
    ExecutionContractError,
    load_execution_contract,
    write_execution_contract,
)
from app.services.report_pack import (
    ReportPack,
    ReportPackExecution,
    ReportPackExecutionStep,
)
from app.services.reviewer import review_render_output


def _project_with_contract(base: Path) -> Path:
    code = base / "code"
    preprocessing = base / "preprocessing"
    output = base / "output"
    code.mkdir(parents=True)
    preprocessing.mkdir()
    output.mkdir()
    (preprocessing / "pre.R").write_text("message('pre')\n")
    (code / "data.R").write_text("message('data')\n")
    (code / "analysis.R").write_text("message('analysis')\n")
    (code / "validate.R").write_text("stopifnot(TRUE)\n")
    (code / "main.R").write_text("quarto::quarto_render()\n")
    (code / "report.qmd").write_text("---\ntitle: Report\n---\n\nReport.\n")
    (code / "_quarto.yml").write_text(
        "project:\n  type: website\n  output-dir: ../output\n  render:\n    - report.qmd\n"
    )

    pack = ReportPack(
        root=base,
        pack_id="test-pack",
        version="1.2.3",
        domain="test",
        name="Test pack",
        entrypoint="code/main.R",
        source="declared",
        manifest_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        execution=ReportPackExecution(
            working_directory="code",
            render="entrypoint",
            steps=(
                ReportPackExecutionStep("preprocess", "preprocessing/pre.R", "data_loader"),
                ReportPackExecutionStep("load-data", "code/data.R", "data_loader"),
                ReportPackExecutionStep("analyze", "code/analysis.R", "analysis"),
                ReportPackExecutionStep("validate", "code/validate.R", "validator"),
            ),
            artifacts=("output/index.html",),
        ),
    )
    target = write_execution_contract(base, pack)
    assert target == base / CONTRACT_NAME
    return base


def _project_with_non_code_contract(base: Path) -> Path:
    report = base / "report"
    output = base / "output"
    report.mkdir(parents=True)
    output.mkdir()
    (report / "report.qmd").write_text(
        "---\ntitle: Report\n---\n\n```{r}\nsessionInfo()\n```\n"
    )
    (report / "_quarto.yml").write_text(
        "project:\n  type: website\n  output-dir: ../output\n  render:\n    - report.qmd\n"
    )
    pack = ReportPack(
        root=base,
        pack_id="non-code-pack",
        version="1.0.0",
        domain="test",
        name="Non-code pack",
        source="declared",
        manifest_sha256="c" * 64,
        source_tree_sha256="d" * 64,
        execution=ReportPackExecution(
            working_directory="report",
            render="incremental",
            steps=(),
            artifacts=("output/index.html",),
        ),
    )
    write_execution_contract(base, pack)
    return base


def test_contract_round_trip_preserves_order_roles_and_artifacts(tmp_path: Path):
    _project_with_contract(tmp_path)

    contract = load_execution_contract(tmp_path)

    assert contract is not None
    assert contract.working_directory == "code"
    assert [step.step_id for step in contract.steps] == [
        "preprocess",
        "load-data",
        "analyze",
        "validate",
    ]
    assert contract.steps[-1].role == "validator"
    assert contract.artifacts == ("output/index.html",)


def test_contract_excludes_explicitly_deleted_execution_source(tmp_path: Path):
    project = _project_with_contract(tmp_path)
    pack = ReportPack(
        root=project,
        pack_id="test-pack",
        version="1.2.3",
        domain="test",
        name="Test pack",
        entrypoint="code/main.R",
        source="declared",
        manifest_sha256="a" * 64,
        source_tree_sha256="b" * 64,
        execution=ReportPackExecution(
            working_directory="code",
            render="entrypoint",
            steps=(
                ReportPackExecutionStep("preprocess", "preprocessing/pre.R", "data_loader"),
                ReportPackExecutionStep("load-data", "code/data.R", "data_loader"),
                ReportPackExecutionStep("analyze", "code/analysis.R", "analysis"),
                ReportPackExecutionStep("validate", "code/validate.R", "validator"),
            ),
            artifacts=("output/index.html",),
        ),
    )
    write_execution_contract(project, pack, excluded_paths=("code/analysis.R",))
    contract = load_execution_contract(project)
    assert contract is not None
    assert [step.step_id for step in contract.steps] == [
        "preprocess",
        "load-data",
        "validate",
    ]


def test_missing_required_contract_does_not_fall_back_to_legacy(tmp_path: Path):
    (tmp_path / "report_pack.yaml").write_text(
        "schema_version: '1.0'\nexecution:\n  working_directory: code\n"
    )

    with pytest.raises(ExecutionContractError, match="required.*missing"):
        load_execution_contract(tmp_path)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda body: body.update({"unexpected": True}), "Unknown execution-contract"),
        (
            lambda body: body["steps"][0].update({"path": "../outside.R"}),
            "safe relative path",
        ),
        (
            lambda body: body["steps"][0].update({"role": "helper"}),
            "Invalid role",
        ),
    ],
)
def test_contract_tampering_fails_closed(
    tmp_path: Path,
    mutation,
    match: str,
):
    _project_with_contract(tmp_path)
    path = tmp_path / CONTRACT_NAME
    body = json.loads(path.read_text())
    mutation(body)
    path.write_text(json.dumps(body))

    with pytest.raises(ExecutionContractError, match=match):
        load_execution_contract(tmp_path)


@pytest.mark.asyncio
async def test_runner_executes_declared_order_then_entrypoint(tmp_path: Path, monkeypatch):
    base = _project_with_contract(tmp_path)
    calls: list[tuple[list[str], str, str | None]] = []

    async def fake_run_command(
        cmd,
        cwd,
        progress_callback=None,
        timeout=1800,
        sandbox_root=None,
    ):
        calls.append((cmd, cwd, sandbox_root))
        if cmd == ["Rscript", "main.R"]:
            (base / "output" / "index.html").write_text("<html>fresh</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(base))

    assert result["status"] == "completed", result
    assert [call[0] for call in calls] == [
        ["Rscript", "../preprocessing/pre.R"],
        ["Rscript", "data.R"],
        ["Rscript", "analysis.R"],
        ["Rscript", "validate.R"],
        ["Rscript", "main.R"],
    ]
    assert {call[1] for call in calls} == {str(base / "code")}
    assert {call[2] for call in calls} == {str(base)}


@pytest.mark.asyncio
async def test_runner_fails_fast_before_validator_and_entrypoint(tmp_path: Path, monkeypatch):
    base = _project_with_contract(tmp_path)
    commands: list[list[str]] = []

    async def fake_run_command(cmd, cwd, **kwargs):
        commands.append(cmd)
        if cmd == ["Rscript", "analysis.R"]:
            return False, "Error: model failed"
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(base))

    assert result["status"] == "failed"
    assert commands[-1] == ["Rscript", "analysis.R"]
    assert ["Rscript", "validate.R"] not in commands
    assert ["Rscript", "main.R"] not in commands


@pytest.mark.asyncio
async def test_runner_rejects_success_with_stale_declared_artifact(tmp_path: Path, monkeypatch):
    base = _project_with_contract(tmp_path)
    stale = base / "output" / "index.html"
    stale.write_text("<html>old result</html>\n")

    async def fake_run_command(cmd, cwd, **kwargs):
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(base))

    assert result["status"] == "failed"
    assert result["errors"][-1]["step"] == "artifacts"
    assert "not refreshed" in result["errors"][-1]["error"]


@pytest.mark.asyncio
async def test_same_project_runs_are_serialized(tmp_path: Path, monkeypatch):
    base = _project_with_contract(tmp_path)
    active = 0
    max_active = 0
    command_count = 0

    async def fake_run_command(cmd, cwd, **kwargs):
        nonlocal active, max_active, command_count
        active += 1
        max_active = max(max_active, active)
        command_count += 1
        await asyncio.sleep(0.002)
        if cmd == ["Rscript", "main.R"]:
            (base / "output" / "index.html").write_text(
                f"<html>run {command_count}</html>\n"
            )
        active -= 1
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    first, second = await asyncio.gather(
        runner.run_project(str(base)),
        runner.run_project(str(base)),
    )

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert max_active == 1


@pytest.mark.asyncio
async def test_runner_consumes_pending_invalidation_and_resumes_from_boundary(
    tmp_path: Path,
    monkeypatch,
):
    base = _project_with_contract(tmp_path)
    pending = base / ".omicsbase" / "invalidation.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({
        "changed_paths": ["code/analysis.R"],
        "resume_from_step": "analyze",
        "invalidated_steps": ["analyze", "validate"],
    }))
    commands: list[list[str]] = []

    async def fake_run_command(cmd, cwd, **kwargs):
        commands.append(cmd)
        if cmd == ["Rscript", "main.R"]:
            (base / "output" / "index.html").write_text("<html>fresh</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(base))

    assert result["status"] == "completed", result
    assert commands == [
        ["Rscript", "analysis.R"],
        ["Rscript", "validate.R"],
        ["Rscript", "main.R"],
    ]
    assert not pending.exists()


@pytest.mark.asyncio
async def test_runner_targets_changed_qmd_when_no_execution_step_is_invalidated(
    tmp_path: Path,
    monkeypatch,
):
    base = _project_with_contract(tmp_path)
    pending = base / ".omicsbase" / "invalidation.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({
        "changed_paths": ["code/report.qmd"],
        "resume_from_step": None,
        "invalidated_steps": [],
        "targeted_pages": ["report.qmd"],
    }))
    commands: list[list[str]] = []

    async def fake_run_command(cmd, cwd, **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["quarto", "render"]:
            (base / "output" / "report.html").write_text("<html>page</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    # A page-only pending invalidation defaults to render-only execution; the
    # caller should not need to know the internal run_data switch.
    result = await runner.run_project(str(base))

    assert result["status"] == "completed", result
    assert commands == [["quarto", "render", "report.qmd"]]
    assert not pending.exists()


@pytest.mark.asyncio
async def test_targeted_run_does_not_consume_skipped_execution_invalidation(
    tmp_path: Path,
    monkeypatch,
):
    base = _project_with_contract(tmp_path)
    pending = base / ".omicsbase" / "invalidation.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({
        "changed_paths": ["code/analysis.R"],
        "resume_from_step": "analyze",
        "invalidated_steps": ["analyze", "validate"],
        "targeted_pages": ["report.qmd"],
    }))

    async def fake_run_command(cmd, cwd, **kwargs):
        if cmd[:2] == ["quarto", "render"]:
            (base / "output" / "report.html").write_text("<html>page</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(
        str(base),
        run_data=False,
        target_pages=["report.qmd"],
    )

    assert result["status"] == "completed"
    assert pending.exists()


@pytest.mark.asyncio
async def test_runner_keeps_pending_invalidation_when_resume_fails(
    tmp_path: Path,
    monkeypatch,
):
    base = _project_with_contract(tmp_path)
    pending = base / ".omicsbase" / "invalidation.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"resume_from_step": "analyze"}))

    async def fake_run_command(cmd, cwd, **kwargs):
        if cmd == ["Rscript", "analysis.R"]:
            return False, "analysis failed"
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(base))

    assert result["status"] == "failed"
    assert pending.exists()


@pytest.mark.asyncio
async def test_targeted_run_uses_incremental_policy_not_entrypoint(tmp_path: Path, monkeypatch):
    base = _project_with_contract(tmp_path)
    commands: list[list[str]] = []

    async def fake_run_command(cmd, cwd, **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["quarto", "render"]:
            (base / "output" / "report.html").write_text("<html>page</html>\n")
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(
        str(base),
        run_data=False,
        target_pages=["report.qmd"],
    )

    assert result["status"] == "completed"
    assert commands == [["quarto", "render", "report.qmd"]]


@pytest.mark.asyncio
async def test_incremental_contract_uses_non_code_working_directory(
    tmp_path: Path,
    monkeypatch,
):
    base = _project_with_non_code_contract(tmp_path)
    calls: list[tuple[list[str], str]] = []

    async def fake_run_command(cmd, cwd, **kwargs):
        calls.append((cmd, cwd))
        return True, "ok"

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(
        str(base),
        run_data=False,
        target_pages=["report.qmd"],
    )

    assert result["status"] == "completed", result
    assert calls == [(["quarto", "render", "report.qmd"], str(base / "report"))]
    assert result["pages"][0]["file"] == "report/report.qmd"


@pytest.mark.asyncio
async def test_runner_rejects_missing_required_contract_before_execution(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "data.R").write_text("stop('must not run')\n")
    (tmp_path / "report_pack.yaml").write_text("execution: {}\n")
    called = False

    async def fake_run_command(*args, **kwargs):
        nonlocal called
        called = True
        return True, ""

    monkeypatch.setattr(runner, "_run_command", fake_run_command)

    result = await runner.run_project(str(tmp_path))

    assert result["status"] == "failed"
    assert result["errors"][0]["step"] == "execution_contract"
    assert called is False


@pytest.mark.asyncio
async def test_docker_mounts_project_root_and_uses_declared_workdir(
    tmp_path: Path,
    monkeypatch,
):
    base = tmp_path
    code = base / "code"
    code.mkdir()
    captured: dict[str, object] = {}

    class FakeStdout:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = FakeStdout()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(runner.settings, "use_docker_sandbox", True)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_exec)

    success, _ = await runner._run_command(
        ["Rscript", "../preprocessing/pre.R"],
        cwd=str(code),
        sandbox_root=str(base),
    )

    assert success is True
    args = list(captured["args"])
    assert args[args.index("-v") + 1] == f"{base}:/workspace"
    assert args[args.index("-w") + 1] == "/workspace/code"


def test_reviewer_uses_contract_sources_without_legacy_data_or_funct(tmp_path: Path):
    base = _project_with_contract(tmp_path)
    (base / "code" / "data.R").unlink()
    # Keep the contract valid by replacing the removed step with the remaining
    # analysis step in the serialized test artifact.
    contract_path = base / CONTRACT_NAME
    body = json.loads(contract_path.read_text())
    body["steps"] = [step for step in body["steps"] if step["path"] != "code/data.R"]
    contract_path.write_text(json.dumps(body))
    (base / "output" / "index.html").write_text(
        "<html><body><nav>menu</nav><main>sessionInfo output</main></body></html>" * 20
    )

    result = review_render_output(str(base))

    assert result["status"] in {"passed", "warning"}
    assert not any(check["name"] == "source_code_data_R" for check in result["checks"])


def test_reviewer_uses_non_code_contract_working_directory(tmp_path: Path):
    base = _project_with_non_code_contract(tmp_path)
    (base / "output" / "index.html").write_text(
        "<html><body><nav>menu</nav><main>sessionInfo output</main></body></html>" * 20
    )

    result = review_render_output(str(base))

    assert result["status"] in {"passed", "warning"}, result
    by_name = {check["name"]: check for check in result["checks"]}
    assert by_name["source_report_quarto_yml"]["status"] == "passed"
    assert by_name["qmd_pages"]["status"] == "passed"
    assert "under report/" in by_name["qmd_pages"]["detail"]


def test_repairs_require_full_pack_rerun_for_any_r_source(tmp_path: Path):
    _project_with_contract(tmp_path)

    assert runner.repairs_require_analysis_rerun(tmp_path, ["code/analysis.R"])
    assert runner.repairs_require_analysis_rerun(tmp_path, ["code/helper.R"])
    assert not runner.repairs_require_analysis_rerun(tmp_path, ["code/report.qmd"])
