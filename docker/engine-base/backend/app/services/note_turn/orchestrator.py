"""Application orchestration for durable NoteThread turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from app.services.note_payloads import note_execution_observation_payload as _note_execution_observation_payload
from app.services.note_store import (
    get_tenant_thread as _get_note_thread_for_tenant,
    thread_summary_payload as _thread_summary_payload,
)
from app.services.note_turn.policy import is_demonstration_request as _is_demonstration_request
from app.services.note_turn_context import (
    build_note_agent_context as _note_agent_context,
    wait_for_note_execution as _wait_for_note_execution,
)

from app.config import settings
from app.models.notes import NoteCell
from app.models.project import Project
from app.schemas.schemas import NoteCellExecutionCreate, NoteThreadTurnRequest
from app.services.note_agent import append_note_cell, conversation_from_cells, stream_note_agent
from app.services.note_turn.events import stream_turn_events
from app.services.note_turn.execution_waiter import observe_execution
from app.services.note_turn.tool_dispatch import NoteTurnToolDispatcher

logger = logging.getLogger(__name__)

async def run_note_turn(
    thread_id: str,
    data: NoteThreadTurnRequest,
    background_tasks: BackgroundTasks,
    db: Session,
    tenant_id: str,
    user_id: str,
):
    """Run one autonomous turn while persisting every visible action as a cell."""
    from fastapi.responses import StreamingResponse
    
    message = data.message.strip()
    demonstration_request = _is_demonstration_request(message)
    if not message:
        raise HTTPException(status_code=422, detail="The notebook message cannot be blank")
    
    thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if thread.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add turns to an archived NoteThread")
    from app.services.agent_plans import (
        append_continuation_step,
        continuation_can_resume,
        continuation_prompt,
        get_continuation_plan,
        mark_continuation_running,
        mark_continuation_consumed,
    )
    from app.services.agent_runs import (
        IdempotencyConflict,
        get_agent_run,
        is_run_task_active,
        session_factory_for,
        register_run_task,
        unregister_run_task,
        approximate_tokens,
        create_or_get_agent_run,
        record_run_telemetry,
        telemetry_from_usage,
        record_stream_event,
        replay_agent_run_stream,
        run_cancel_requested,
        serialize_agent_run,
        transition_agent_run,
        TOKEN_CHUNK_FLUSH_CHARS,
    )
    
    request_payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data.dict()
    try:
        run, run_created = create_or_get_agent_run(
            db,
            tenant_id=tenant_id,
            owner_id=user_id,
            surface="notes",
            idempotency_scope=f"notes:{thread_id}:turn",
            idempotency_key=data.idempotency_key,
            request_payload=request_payload,
            project_id=str(thread.project_id) if thread.project_id else None,
            note_thread_id=str(thread.id),
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    
    continuation_plan = get_continuation_plan(run)
    continuation_resume = (
        not run_created
        and continuation_can_resume(run)
        and not is_run_task_active(str(run.id))
    )
    resume_existing_run = (
        not run_created
        and not is_run_task_active(str(run.id))
        and bool(run.resumable)
        and (continuation_resume or (run.status == "paused" and not continuation_plan))
    )
    if not run_created and not resume_existing_run:
        async def replay_existing_run():
            async for replay_event in replay_agent_run_stream(
                str(run.id),
                tenant_id,
                session_factory=session_factory_for(db),
            ):
                yield json.dumps(replay_event, default=str) + "\n"
    
        return StreamingResponse(
            replay_existing_run(),
            media_type="application/x-ndjson",
            headers={"X-Agent-Run-ID": str(run.id), "X-Agent-Run-Replayed": "true"},
        )
    
    turn_id = str(run.id)
    if resume_existing_run:
        if continuation_resume and continuation_plan:
            mark_continuation_running(run)
        transition_agent_run(db, run, "running", event_type="run_resumed")
        run.result_payload = None
        db.commit()
        existing_cells = list(thread.cells)
        user_cell = next(
            (
                cell
                for cell in existing_cells
                if any(
                    str((revision.revision_metadata or {}).get("turn_id")) == turn_id
                    and revision.cell_type == "agent"
                    for revision in cell.revisions
                )
            ),
            None,
        )
        if user_cell is None:
            raise HTTPException(status_code=409, detail="Paused run has no persisted user cell")
        prior_cells = [cell for cell in existing_cells if str(cell.id) != str(user_cell.id)]
    else:
        prior_cells = list(thread.cells)
        user_cell = append_note_cell(
            db,
            thread,
            cell_type="agent",
            content=message,
            metadata={
                "turn_id": turn_id,
                "role": "user",
                "attachments": [a.model_dump() for a in data.attachments] if data.attachments else [],
            },
            created_by=user_id,
        )
        transition_agent_run(db, run, "running", event_type="run_started")
        db.commit()
    active_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    if active_thread.title_source == "default":
        from app.services.titles import claim_note_auto_title, generate_title, title_context
    
        expected_title = str(active_thread.title)
        try:
            proposed_title = await generate_title(
                kind="note",
                user_intent=message,
                context=title_context(
                    file_names=[str(item.name or "") for item in data.attachments]
                ),
            )
            claim_note_auto_title(
                db,
                thread_id=str(active_thread.id),
                expected_title=expected_title,
                proposed_title=proposed_title,
            )
        except Exception as exc:
            logger.warning("Note auto-titling failed for %s: %s", active_thread.id, exc)
        db.expire_all()
        active_thread = _get_note_thread_for_tenant(db, thread_id, tenant_id)
    
    context = _note_agent_context(db, active_thread)
    agent_message = continuation_prompt(continuation_plan) if continuation_resume and continuation_plan else message
    async def _execution_observation(
        execution_payload: dict,
        cell_payload: dict,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await observe_execution(
            db=db,
            execution_payload=execution_payload,
            cell_payload=cell_payload,
            timeout_seconds=timeout_seconds,
            turn_id=turn_id,
            wait_enabled=settings.note_execution_agent_wait_enabled,
            cancel_check=cancel_requested_now,
            wait_for_execution=_wait_for_note_execution,
            observation_payload=_note_execution_observation_payload,
        )
    
    request_db = db
    worker_session_factory = session_factory_for(request_db)

    def cancel_requested_now() -> bool:
        check_db = worker_session_factory()
        try:
            return run_cancel_requested(check_db, str(run.id))
        finally:
            check_db.close()

    worker_db = worker_session_factory()
    worker_thread = _get_note_thread_for_tenant(worker_db, thread_id, tenant_id)
    worker_run = get_agent_run(worker_db, str(run.id), tenant_id)
    if worker_run is None:
        worker_db.close()
        raise HTTPException(status_code=404, detail="Agent run disappeared before execution")
    worker_user_cell = (
        worker_db.query(NoteCell)
        .filter(NoteCell.id == str(user_cell.id))
        .first()
    )
    if worker_user_cell is None:
        worker_db.close()
        raise HTTPException(status_code=404, detail="Note cell disappeared before execution")
    worker_prior_cells = [
        cell for cell in worker_thread.cells if str(cell.id) != str(user_cell.id)
    ]
    request_db.close()
    db = worker_db
    thread = worker_thread
    active_thread = worker_thread
    run = worker_run
    user_cell = worker_user_cell
    prior_cells = worker_prior_cells
    context = _note_agent_context(worker_db, worker_thread)
    
    tool_dispatcher = NoteTurnToolDispatcher(
        db=db,
        thread_id=thread_id,
        tenant_id=tenant_id,
        turn_id=turn_id,
        message=message,
        data=data,
        background_tasks=background_tasks,
        execution_observation=_execution_observation,
    )
    knowledge_search_handler = tool_dispatcher.knowledge_search_handler
    action_handler = tool_dispatcher.action_handler
    
    event_stream = stream_turn_events(
        db=db,
        run=run,
        thread_id=thread_id,
        tenant_id=tenant_id,
        turn_id=turn_id,
        message=message,
        data=data,
        agent_message=agent_message,
        user_cell=user_cell,
        active_thread=active_thread,
        prior_cells=prior_cells,
        context=context,
        continuation_resume=continuation_resume,
        action_handler=action_handler,
        knowledge_search_handler=knowledge_search_handler,
        tool_dispatcher=tool_dispatcher,
        worker_session_factory=worker_session_factory,
        demonstration_request=demonstration_request,
    )
    
    async def consume_run():
        worker_task = asyncio.current_task()
        try:
            async for _ in event_stream:
                pass
        except asyncio.CancelledError:
            pause_db = worker_session_factory()
            try:
                paused_run = get_agent_run(pause_db, str(run.id), tenant_id)
                if paused_run and paused_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if paused_run.cancel_requested else "paused"
                    if paused_run.status != target:
                        transition_agent_run(
                            pause_db,
                            paused_run,
                            target,
                            event_type="run_cancelled" if target == "cancelled" else "run_paused",
                            payload={"reason": "worker interrupted"},
                        )
                    pause_db.commit()
            finally:
                pause_db.close()
            raise
        except Exception as exc:
            logger.exception("NoteThread worker failed: %s", exc)
            failure_db = worker_session_factory()
            try:
                failed_run = get_agent_run(failure_db, str(run.id), tenant_id)
                if failed_run and failed_run.status not in {"completed", "failed", "cancelled"}:
                    target = "cancelled" if failed_run.cancel_requested else "failed"
                    transition_agent_run(
                        failure_db,
                        failed_run,
                        target,
                        event_type="run_cancelled" if target == "cancelled" else "run_failed",
                        payload={"error": str(exc)[:1000]},
                    )
                    failure_db.commit()
            finally:
                failure_db.close()
        finally:
            try:
                close_stream = getattr(event_stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            except Exception:
                logger.debug("NoteThread event stream close failed", exc_info=True)
            unregister_run_task(str(run.id), worker_task)
            resume_db = worker_session_factory()
            try:
                from app.services.agent_continuations import dispatch_ready_continuations
    
                dispatch_ready_continuations(resume_db, run_id=str(run.id))
            finally:
                resume_db.close()
            worker_db.close()
    
    worker_task = asyncio.create_task(consume_run(), name=f"note-agent-{run.id}")
    register_run_task(str(run.id), worker_task)
    
    async def durable_stream():
        async for replay_event in replay_agent_run_stream(
            str(run.id),
            tenant_id,
            session_factory=worker_session_factory,
        ):
            yield json.dumps(replay_event, default=str) + "\n"
    
    return StreamingResponse(
        durable_stream(),
        media_type="application/x-ndjson",
        headers={
            "X-Agent-Run-ID": str(run.id),
            "X-Agent-Run-Transport": "durable-replay",
        },
    )
    
