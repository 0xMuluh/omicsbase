"""Guided elicitation: planner clarification requests, answers, and API endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.project import Project
from app.schemas.schemas import (
    AnalysisPlan,
    ClarificationAnswer,
    ClarificationRequest,
)
from app.services import planner

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MANIFEST_WITH_GROUPS = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [
        {"column": "treatment", "levels": ["control", "disease"], "file": "metadata.tsv"},
        {"column": "sex", "levels": ["M", "F"], "file": "metadata.tsv"},
    ],
}

MANIFEST_NO_GROUPS = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [],
}

MANIFEST_UNCLASSIFIED_CANDIDATE = {
    "status": "ready",
    "domain": "microbiome",
    "files": [
        {"name": "feature_table.tsv", "role": "feature_table", "format": "tsv"},
        {"name": "metadata.tsv", "role": "metadata", "format": "tsv"},
    ],
    "grouping_candidates": [
        {"column": "treatment", "levels": [], "file": "metadata.tsv"},
    ],
}


def test_fallback_asks_when_grouping_unresolved():
    request = planner._build_default_plan(
        "Compare two groups",
        [],
        study_manifest=MANIFEST_NO_GROUPS,
    )
    assert isinstance(request, ClarificationRequest)
    ids = [q.id for q in request.questions]
    assert "grouping_variable" in ids
    assert "differential_abundance_method" not in ids
    assert request.questions[0].allow_custom is True


def test_fallback_asks_method_when_candidate_unclassified():
    request = planner._build_default_plan(
        "Compare two groups",
        [],
        study_manifest=MANIFEST_UNCLASSIFIED_CANDIDATE,
    )
    assert isinstance(request, ClarificationRequest)
    ids = [q.id for q in request.questions]
    assert "grouping_variable" in ids
    assert "differential_abundance_method" in ids


def test_fallback_plans_when_grouping_present():
    plan = planner._build_default_plan(
        "Compare two groups",
        [],
        study_manifest=MANIFEST_WITH_GROUPS,
    )
    assert isinstance(plan, AnalysisPlan)
    assert plan.grouping_variable == "treatment"
    assert plan.group_levels == ["control", "disease"]
    assert plan.study_type == "two_group_comparison"


def test_fallback_uses_answered_grouping():
    plan = planner._build_default_plan(
        "Compare groups",
        [],
        study_manifest=MANIFEST_NO_GROUPS,
        answers={
            "grouping_variable": ["sex"],
            "differential_abundance_method": ["ALDEx2 only"],
        },
    )
    assert isinstance(plan, AnalysisPlan)
    assert plan.grouping_variable == "sex"
    da_step = next(step for step in plan.workflow if step.id == "differential_abundance")
    assert da_step.ensemble_methods == [{"id": "aldex2", "name": "ALDEx2", "r_package": "ALDEx2", "role": "primary"}]


def test_fallback_keeps_ensemble_when_run_all_chosen():
    plan = planner._build_default_plan(
        "Compare groups",
        [],
        study_manifest=MANIFEST_NO_GROUPS,
        answers={"grouping_variable": ["sex"], "differential_abundance_method": ["Run all methods as an ensemble (recommended)"]},
    )
    assert isinstance(plan, AnalysisPlan)
    da_step = next(step for step in plan.workflow if step.id == "differential_abundance")
    assert len(da_step.ensemble_methods) == 4


@pytest.mark.asyncio
async def test_llm_path_returns_clarification_request(monkeypatch):
    async def fake_llm(**kwargs):
        return json_dumps({
            "needs_clarification": {
                "message": "The metadata grouping is unclear.",
                "questions": [
                    {
                        "id": "grouping_variable",
                        "prompt": "Which column defines the groups?",
                        "options": ["treatment", "sex"],
                        "allow_custom": True,
                    }
                ],
            }
        })

    monkeypatch.setattr(planner, "call_llm", fake_llm)
    result = await planner.generate_plan(
        "Compare groups",
        [],
        study_manifest=MANIFEST_NO_GROUPS,
        clarifications=None,
    )
    assert isinstance(result, ClarificationRequest)
    assert result.questions[0].id == "grouping_variable"


@pytest.mark.asyncio
async def test_llm_path_returns_plan(monkeypatch):
    async def fake_llm(**kwargs):
        return json_dumps({
            "project_name": "Treatment Effects",
            "domain": "microbiome",
            "study_type": "two_group_comparison",
            "question": "Compare treatment vs control",
            "detected_inputs": [],
            "grouping_variable": "treatment",
            "group_levels": ["control", "disease"],
            "workflow": [
                {
                    "id": "alpha_diversity",
                    "name": "Alpha Diversity Profiling",
                    "classification": "standard",
                    "recipe_id": "microbiome.alpha_diversity",
                    "enabled": True,
                }
            ],
            "estimated_runtime_minutes": 5,
        })

    monkeypatch.setattr(planner, "call_llm", fake_llm)
    result = await planner.generate_plan(
        "Compare treatment vs control",
        [],
        study_manifest=MANIFEST_WITH_GROUPS,
        clarifications=[ClarificationAnswer(id="grouping_variable", values=["treatment"])],
    )
    assert isinstance(result, AnalysisPlan)
    assert result.grouping_variable == "treatment"


def test_run_planning_persists_pending_clarifications(monkeypatch):
    from app.tasks import analysis as tasks_module
    from app.services import agent_runtime

    async def fake_generate_plan(**kwargs):
        return ClarificationRequest(
            message="Need a decision.",
            questions=[
                {"id": "grouping_variable", "prompt": "Which column?", "options": ["a", "b"], "allow_custom": True}
            ],
        )

    monkeypatch.setattr(planner, "generate_plan", fake_generate_plan)
    monkeypatch.setattr(tasks_module, "_get_db_session", lambda: TestingSessionLocal())
    monkeypatch.setattr(tasks_module, "_update_job", lambda db, job_id, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "set_agent_state", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "record_agent_action", lambda *a, **k: None)

    from app.models.project import Job

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        project = Project(
            name="Test", question="Compare groups", status="planning",
            tenant_id="default_tenant", owner_id="default_user",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        job = Job(project_id=str(project.id), job_type="plan", status="running")
        db.add(job)
        db.commit()
        db.refresh(job)

        tasks_module.run_planning(str(project.id), str(job.id))

        db.refresh(project)
        assert project.status == "needs_clarification"
        pending = (project.agent_memory or {}).get("pending_clarifications")
        assert pending and pending["questions"][0]["id"] == "grouping_variable"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_chat_origin_planning_never_auto_builds(monkeypatch):
    from app.tasks import analysis as tasks_module
    from app.services import agent_runtime, document_classify

    async def fake_generate_plan(**kwargs):
        return planner._build_default_plan(
            kwargs["question"],
            kwargs["file_summaries"],
            study_manifest=kwargs["study_manifest"],
        )

    async def no_plan_sources(files):
        return []

    monkeypatch.setattr(planner, "generate_plan", fake_generate_plan)
    monkeypatch.setattr(document_classify, "agentic_plan_sources", no_plan_sources)
    monkeypatch.setattr(tasks_module, "_get_db_session", lambda: TestingSessionLocal())
    monkeypatch.setattr(tasks_module, "_update_job", lambda db, job_id, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "set_agent_state", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "record_agent_action", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "refresh_project_memory", lambda *a, **k: None)

    from app.models.project import Job

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        project = Project(
            name="Chat plan",
            question="Compare groups",
            status="planning",
            tenant_id="default_tenant",
            owner_id="default_user",
            auto_build=True,
            study_manifest=MANIFEST_WITH_GROUPS,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        job = Job(project_id=str(project.id), job_type="plan", status="running")
        db.add(job)
        db.commit()
        db.refresh(job)

        result = tasks_module.run_planning(
            str(project.id),
            str(job.id),
            allow_auto_build=False,
        )

        db.refresh(project)
        assert project.status == "planned"
        assert result["auto_build"] is False
        assert (
            db.query(Job)
            .filter(Job.project_id == str(project.id), Job.job_type == "generate")
            .count()
            == 0
        )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def json_dumps(data: dict) -> str:
    import json
    return json.dumps(data)


def test_fallback_rejects_custom_plan_instead_of_substituting_default():
    with pytest.raises(
        planner.CustomPlanRequiresPlannerError,
        match="No default workflow was substituted",
    ):
        planner._build_default_plan(
            "Compare two groups",
            [],
            custom_plan_text="Use a longitudinal mixed-effects model with subject random intercepts.",
            study_manifest=MANIFEST_WITH_GROUPS,
        )


@pytest.mark.asyncio
async def test_unconfigured_planner_rejects_custom_plan(monkeypatch):
    from app.services import providers

    monkeypatch.setattr(providers, "is_configured", lambda _provider: False)

    with pytest.raises(
        planner.CustomPlanRequiresPlannerError,
        match="No default workflow was substituted",
    ):
        await planner.generate_plan(
            "Compare treatment vs control",
            [],
            custom_plan_text="Use a longitudinal mixed-effects model with subject random intercepts.",
            study_manifest=MANIFEST_WITH_GROUPS,
        )


@pytest.mark.asyncio
async def test_llm_parse_failure_rejects_custom_plan(monkeypatch):
    from app.services import providers

    async def fail(**_kwargs):
        raise ValueError("invalid planner response")

    monkeypatch.setattr(providers, "is_configured", lambda _provider: True)
    monkeypatch.setattr(planner, "call_llm", fail)

    with pytest.raises(
        planner.CustomPlanRequiresPlannerError,
        match="No default workflow was substituted",
    ):
        await planner.generate_plan(
            "Compare treatment vs control",
            [],
            custom_plan_text="Use a longitudinal mixed-effects model with subject random intercepts.",
            study_manifest=MANIFEST_WITH_GROUPS,
        )
