"""Tests for automatic mid-job guidance follow-up."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.guidance_followup import decide_guidance_action
from app.services.provider_errors import LLMQuotaError
from app.tasks import analysis


@pytest.mark.asyncio
async def test_decide_guidance_falls_back_to_edit(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.guidance_followup.call_llm", boom)
    project = SimpleNamespace(
        analysis_plan={"domain": "microbiome", "workflow": []},
        study_manifest={"domain": "microbiome"},
    )
    decision = await decide_guidance_action(project, "Add a caption to alpha diversity")
    assert decision["action"] == "edit_project"
    assert "caption" in decision["instruction"]


@pytest.mark.asyncio
async def test_decide_guidance_does_not_turn_quota_failure_into_edit(monkeypatch):
    async def quota_failure(**kwargs):
        raise LLMQuotaError(
            "qwen",
            "quota exhausted",
            code="AllocationQuota.FreeTierOnly",
            status_code=403,
        )

    monkeypatch.setattr("app.services.guidance_followup.call_llm", quota_failure)
    project = SimpleNamespace(
        analysis_plan={"domain": "microbiome", "workflow": []},
        study_manifest={"domain": "microbiome"},
    )

    with pytest.raises(LLMQuotaError):
        await decide_guidance_action(project, "Add a caption to alpha diversity")


def test_schedule_pending_guidance_dispatches_followup(monkeypatch):
    project = SimpleNamespace(
        id="project-1",
        status="completed",
        agent_memory={},
        agent_actions=[],
        analysis_plan={"domain": "microbiome", "workflow": []},
    )
    pending = [
        {
            "content": "Switch beta distance to Jaccard",
            "source": "user",
            "status": "queued",
        }
    ]
    db = SimpleNamespace()
    db.add = lambda item: setattr(item, "id", "job-guidance-1")
    db.commit = lambda: None
    db.refresh = lambda item: None
    dispatched = {}

    class _FakeJob:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = None

    monkeypatch.setattr("app.models.project.Job", _FakeJob)
    monkeypatch.setattr(
        "app.services.agent_runtime.consume_pending_guidance",
        lambda _db, _project: pending,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.set_agent_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.record_agent_action",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.record_project_message",
        lambda *_args, **_kwargs: SimpleNamespace(id="msg-1"),
    )
    monkeypatch.setattr(analysis.settings, "task_backend", "celery")

    class _Task:
        @staticmethod
        def delay(*args, **kwargs):
            dispatched["args"] = args
            dispatched["kwargs"] = kwargs

    monkeypatch.setattr(analysis, "run_guidance_followup", _Task)

    job_id = analysis._schedule_pending_guidance_followup(db, project, "project-1")
    assert job_id == "job-guidance-1"
    assert dispatched["kwargs"]["instruction"] == "Switch beta distance to Jaccard"
    assert project.status == "rendering"
