from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.schemas import AnalysisPlan
from app.services.capability_contract import (
    CapabilityContractError,
    bind_plan_recipes,
    resolve_plan_capabilities,
)
from app.services.report_pack import ReportPack, ReportPackCapability


def _pack(root: Path) -> ReportPack:
    return ReportPack(
        root=root,
        pack_id="declared-pack",
        version="1",
        domain="microbiome",
        name="Declared pack",
        capabilities=(ReportPackCapability("alpha"),),
    )


def _plan(**kwargs) -> AnalysisPlan:
    values = {
        "project_name": "Capability selection",
        "domain": "microbiome",
        "study_type": "exploratory",
        "question": "Describe the study",
        "workflow": [],
    }
    values.update(kwargs)
    return AnalysisPlan(**values)


def test_omitted_capabilities_fail_closed():
    with pytest.raises(CapabilityContractError, match="must explicitly select"):
        resolve_plan_capabilities(_pack(Path(".")), _plan())


def test_explicit_empty_capabilities_are_not_expanded():
    contract = resolve_plan_capabilities(
        _pack(Path(".")),
        _plan(capabilities=[]),
    )
    assert contract.selected == ()


def test_explicit_capabilities_are_resolved():
    contract = resolve_plan_capabilities(
        _pack(Path(".")),
        _plan(capabilities=["alpha"]),
    )
    assert [item.capability.capability_id for item in contract.selected] == ["alpha"]


def test_invalid_report_pack_selection_fails_closed():
    with pytest.raises(CapabilityContractError, match="could not be resolved"):
        bind_plan_recipes(_plan(report_pack_id="missing-pack"))


def test_null_report_pack_stays_null_after_bind():
    plan = bind_plan_recipes(_plan(report_pack_id=None, domain="microbiome"))
    assert plan.report_pack_id is None


def test_empty_report_pack_stays_null_after_bind():
    plan = bind_plan_recipes(_plan(report_pack_id="", domain="microbiome"))
    assert plan.report_pack_id is None


def test_explicit_report_pack_is_kept_after_bind():
    plan = bind_plan_recipes(_plan(report_pack_id="microbiome-diversity", capabilities=[]))
    assert plan.report_pack_id == "microbiome-diversity"