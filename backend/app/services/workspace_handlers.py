"""Tool handler closures shared by the streaming API and headless agent jobs.

One implementation of each mutating/grounding handler serves both loop
entrances (the workspace chat stream and the pipeline adapter jobs), so a
headless build behaves exactly like an in-chat build.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


def make_inline_action_handler(db, project) -> Callable[[str, dict], dict]:
    """Data acquisition tools (import_package_data, fetch_url) with journaling."""
    from app.services.agent_runtime import record_agent_action
    from app.services.data_acquisition import fetch_url_into_study, import_package_dataset

    def inline_action_handler(action: str, arguments: dict) -> dict:
        if action == "import_package_data":
            result = import_package_dataset(
                db,
                project,
                package=str(arguments.get("package") or ""),
                dataset=str(arguments.get("dataset") or ""),
                role=str(arguments.get("role") or "auto"),
            )
        elif action == "fetch_url":
            result = fetch_url_into_study(
                db,
                project,
                url=str(arguments.get("url") or ""),
                filename=arguments.get("filename"),
                role=str(arguments.get("role") or "auto"),
            )
        else:
            result = {"status": "error", "error": f"Unsupported inline action: {action}"}

        db.refresh(project)
        status = "completed" if result.get("status") != "error" else "failed"
        record_agent_action(
            db,
            project,
            action,
            status,
            (
                f"{action} completed"
                if status == "completed"
                else f"{action} failed: {result.get('error', 'unknown error')}"
            ),
            {"arguments": arguments, "result": result},
        )
        return result

    return inline_action_handler


def make_knowledge_search_handler(db) -> Callable[[dict], dict]:
    """Bioconductor book search grounded against the knowledge base."""
    from app.services.bioc_knowledge import search_bioc_knowledge

    def knowledge_search_handler(arguments: dict) -> dict:
        try:
            limit = max(1, min(8, int(arguments.get("limit") or 5)))
        except (TypeError, ValueError):
            limit = 5
        return search_bioc_knowledge(
            db,
            str(arguments.get("query") or ""),
            channel=str(arguments.get("channel") or "stable"),
            limit=limit,
            source_slug=arguments.get("book") or None,
        )

    return knowledge_search_handler


def make_plan_handler(db, project) -> Callable[[dict], dict]:
    """Validate and persist the model-authored analysis plan."""
    from pydantic import ValidationError

    from app.schemas.schemas import AnalysisPlan
    from app.services.agent_runtime import record_agent_action

    def plan_handler(arguments: dict) -> dict:
        import json as _json

        raw = arguments.get("plan")
        if isinstance(raw, str):
            # Some providers serialize nested objects as JSON-encoded text.
            # Normalize below the model rather than making it guess.
            try:
                raw = _json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        if not isinstance(raw, dict) or not raw:
            return {
                "status": "error",
                "error": (
                    "set_plan requires a 'plan' argument that is a JSON object "
                    "(not a string, not JSON-encoded text). Pass the plan fields "
                    "directly as a nested object: {\"plan\": {\"project_name\": ...}}."
                ),
            }
        try:
            plan = AnalysisPlan(**raw)
        except ValidationError as exc:
            details = [
                f"{'.'.join(str(part) for part in error.get('loc', ()) or ()) or 'plan'}: {error.get('msg')}"
                for error in exc.errors(include_url=False)
            ]
            return {
                "status": "error",
                "error": "Plan validation failed — fix these fields and call set_plan again.",
                "details": details[:20],
            }
        pack_id = str(plan.report_pack_id or "").strip()
        template_note = None
        if pack_id:
            from app.services.spawner import report_pack_catalog

            valid_ids = sorted(report_pack_catalog().keys())
            if pack_id not in valid_ids:
                template_note = (
                    f"Ignored unknown report_pack_id {pack_id!r}; "
                    f"valid template ids are: {', '.join(valid_ids) or '(none)'}."
                )
                plan.report_pack_id = None
        project.analysis_plan = plan.model_dump()
        record_agent_action(
            db,
            project,
            "plan",
            "completed",
            f"Analysis plan set: {len(plan.workflow or [])} workflow steps",
            {"report_pack_id": plan.report_pack_id, "study_type": plan.study_type},
        )
        db.commit()
        response = {
            "status": "ok",
            "summary": f"Plan stored: {len(plan.workflow or [])} workflow steps, report_pack_id={plan.report_pack_id}",
            "next": "The plan is persisted. The user reviews it before the build starts.",
        }
        if template_note:
            response["note"] = template_note
        return response

    return plan_handler


def make_render_handler(db, project) -> Callable[[dict], Awaitable[dict]]:
    """Render inside the agent turn, with full job bookkeeping.

    The model observes the structured result (status, per-page errors, log
    tails) in the same conversation and repairs source itself.
    """
    import json as _json
    import time as _time

    from app.models.project import Job
    from app.services.agent_runtime import (
        record_agent_action,
        record_project_message,
        refresh_project_memory,
        set_agent_state,
    )
    from app.services.runner import run_project

    async def render_handler(arguments: dict) -> dict:
        job = Job(project_id=str(project.id), job_type="render", status="running")
        db.add(job)
        db.commit()
        db.refresh(job)
        set_agent_state(db, project, "rendering", "Rendering report")
        record_agent_action(db, project, "render", "started", "Rendering report", job_id=str(job.id))

        progress_log: list[dict] = []
        render_logs: list[str] = []

        def progress_callback(step_id: str, status: str, line: str = ""):
            if line:
                render_logs.append(line)
            entry = {"step": step_id, "status": status}
            existing = next((item for item in reversed(progress_log) if item["step"] == step_id), None)
            if existing:
                existing.update(entry)
            else:
                progress_log.append(entry)
            try:
                job.progress = list(progress_log)
                job.logs = "\n".join(render_logs[-200:])
                db.commit()
            except Exception:
                db.rollback()

        started = _time.monotonic()
        try:
            result = await run_project(
                project_dir=str(project.project_dir),
                progress_callback=progress_callback,
            )
        except Exception as exc:
            logger.exception("Inline render failed for project %s", project.id)
            job.status = "failed"
            job.error = str(exc)
            db.commit()
            record_agent_action(db, project, "render", "failed", str(exc), job_id=str(job.id))
            return {"status": "error", "error": str(exc), "job_id": str(job.id)}

        elapsed = round(_time.monotonic() - started, 1)
        completed = result.get("status") == "completed"
        job.status = "completed" if completed else "failed"
        job.progress = list(progress_log)
        job.logs = "\n".join(render_logs)
        if result.get("errors"):
            job.error = _json.dumps(result["errors"])
        project.status = "completed" if completed else "failed"
        db.commit()
        jobs = db.query(Job).filter(Job.project_id == str(project.id)).order_by(Job.created_at.desc()).all()
        refresh_project_memory(db, project, jobs=jobs)
        set_agent_state(
            db,
            project,
            "completed" if completed else "failed",
            "Report rendered successfully" if completed else "Render failed; failure details returned to the agent",
        )
        record_agent_action(
            db,
            project,
            "render",
            "completed" if completed else "failed",
            f"Inline render {'completed' if completed else 'failed'} in {elapsed}s",
            {"errors": result.get("errors")},
            job_id=str(job.id),
        )
        if completed:
            record_project_message(
                db,
                project,
                "assistant",
                "The report rendered successfully.",
                metadata={"job_id": str(job.id), "status": "completed", "rendered_by": "agent_loop"},
            )
        return {
            "status": "completed" if completed else "error",
            "render_status": result.get("status"),
            "failed_page": result.get("failed_page"),
            "failed_pages": result.get("failed_pages"),
            "errors": result.get("errors"),
            "pages": result.get("pages"),
            "logs_tail": render_logs[-80:],
            "elapsed_seconds": elapsed,
            "job_id": str(job.id),
        }

    return render_handler
