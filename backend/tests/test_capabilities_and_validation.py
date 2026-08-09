from __future__ import annotations

from pathlib import Path

import pytest

from app.services.capability_contract import (
    CapabilityContractError,
    resolve_plan_capabilities,
    validate_capability_bindings,
    validate_plan_parameter_bindings,
)
from app.services.edit_validation import validate_text
from app.services.incremental_invalidation import plan_invalidation
from app.services.report_pack import ReportPack, ReportPackCapability, ReportPackExecution, ReportPackExecutionStep, load_report_pack


def test_builtin_packs_expose_capabilities():
    pack = load_report_pack(Path(__file__).parents[2] / "templates" / "microbiome" / "microbiota_diversity_pipeline")
    assert pack.capabilities
    contract = resolve_plan_capabilities(pack, {"capabilities": [pack.capabilities[0].capability_id]})
    assert contract.selected[0].capability.capability_id == pack.capabilities[0].capability_id


def test_unknown_plan_capability_is_rejected():
    pack = ReportPack(root=Path("."), pack_id="x", version="1", domain="x", name="x", capabilities=(ReportPackCapability("known"),))
    with pytest.raises(CapabilityContractError):
        resolve_plan_capabilities(pack, {"capabilities": ["missing"]})


def test_capability_validator_bindings_use_structured_source_validation(tmp_path):
    validator = tmp_path / "code" / "validate.R"
    validator.parent.mkdir(parents=True)
    validator.write_text("stopifnot(TRUE)\n")
    contract = resolve_plan_capabilities(
        ReportPack(
            root=tmp_path,
            pack_id="x",
            version="1",
            domain="x",
            name="x",
            capabilities=(ReportPackCapability("known", validators=("code/validate.R",)),),
        ),
        {"capabilities": ["known"]},
    )
    result = validate_capability_bindings(tmp_path, contract)
    assert result.valid
    assert "capability_validators" in result.checks


def test_capability_validator_missing_file_fails_preflight(tmp_path):
    contract = resolve_plan_capabilities(
        ReportPack(
            root=tmp_path,
            pack_id="x",
            version="1",
            domain="x",
            name="x",
            capabilities=(ReportPackCapability("known", validators=("code/validate.R",)),),
        ),
        {"capabilities": ["known"]},
    )
    result = validate_capability_bindings(tmp_path, contract)
    assert not result.valid
    assert any(issue.code == "missing_file" for issue in result.issues)


def test_structured_source_validation_reports_frontmatter_and_r_errors():
    qmd = validate_text("code/page.qmd", "---\ntitle: [broken\n---\n# Page\n")
    assert not qmd.valid
    assert any(issue.code == "invalid_frontmatter" for issue in qmd.issues)
    r = validate_text("code/analysis.R", "x <- (1 + 2\n")
    assert not r.valid
    assert any(issue.code == "unbalanced_r" for issue in r.issues)


def test_invalidation_resumes_from_earliest_affected_step(tmp_path):
    pack = ReportPack(
        root=tmp_path,
        pack_id="demo",
        version="1",
        domain="demo",
        name="Demo",
        execution=ReportPackExecution(
            working_directory="code",
            render="incremental",
            steps=(
                ReportPackExecutionStep("load", "code/load.R", "data_loader"),
                ReportPackExecutionStep("fit", "code/fit.R", "analysis"),
                ReportPackExecutionStep("validate", "code/validate.R", "validator"),
            ),
            artifacts=("output/index.html",),
        ),
        capabilities=(ReportPackCapability("demo", sources=("code/fit.R",), execution_steps=("fit",), outputs=("output/index.html",)),),
    )
    plan = plan_invalidation(pack, ["code/fit.R", "code/page.qmd"])
    assert plan.resume_from_step == "fit"
    assert plan.invalidated_steps == ("fit", "validate")
    assert plan.targeted_pages == ("page.qmd",)



def test_required_capability_parameters_are_checked_when_plan_opts_in(tmp_path):
    pack = ReportPack(
        root=tmp_path,
        pack_id="x",
        version="1",
        domain="x",
        name="x",
        capabilities=(ReportPackCapability("known", parameters={"grouping_variable": "required", "distance": "optional"}),),
    )
    # Legacy plans remain diagnosable without becoming invalid by default.
    assert validate_plan_parameter_bindings(pack, {"capabilities": ["known"]}) == {"known": ["grouping_variable"]}
    with pytest.raises(CapabilityContractError, match="grouping_variable"):
        validate_plan_parameter_bindings(
            pack,
            {"capabilities": ["known"], "parameters": {"distance": "bray"}},
        )
    assert validate_plan_parameter_bindings(
        pack,
        {"capabilities": ["known"], "grouping_variable": "condition", "parameters": {"distance": "bray"}},
    ) == {}
