"""Tests for push job events and mid-job guidance."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import job_events
from app.services.agent_runtime import (
    consume_pending_guidance,
    queue_pending_guidance,
)


class _Db:
    def commit(self):
        return None


@pytest.mark.asyncio
async def test_local_project_event_push_reaches_subscriber(monkeypatch):
    monkeypatch.setattr(job_events, "_force_local", True)
    project_id = "project-push-1"
    received = []

    async def listen():
        async for event in job_events.subscribe_project_events(project_id):
            received.append(event)
            break

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.05)
    job_events.publish_project_event(project_id, {"job_status": "running"})
    await asyncio.wait_for(task, timeout=2)

    assert received[0]["project_id"] == project_id
    assert received[0]["job_status"] == "running"


def test_pending_guidance_queues_and_consumes():
    project = SimpleNamespace(id="p1", agent_memory={"state": "rendering", "summary": "Rendering"})
    db = _Db()

    queued = queue_pending_guidance(db, project, "Switch distance to Jaccard")
    assert queued["content"] == "Switch distance to Jaccard"
    assert len(project.agent_memory["pending_guidance"]) == 1

    pending = consume_pending_guidance(db, project)
    assert len(pending) == 1
    assert project.agent_memory["pending_guidance"] == []
