"""Async analysis tasks with optional Celery support."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Optional Celery integration
try:
    from celery import Celery
    celery_app = Celery(
        "omicsbase",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        include=["app.tasks.notes"],
    )
    task_decorator = celery_app.task(bind=True)
    celery_app.conf.beat_schedule = {
        "sync-bioc-knowledge-weekly": {
            "task": "app.tasks.analysis.sync_bioc_knowledge",
            "schedule": float(settings.bioc_knowledge_sync_interval_hours) * 3600,
        }
    }
except ImportError:
    celery_app = None

    def task_decorator(func):
        """Fallback decorator when Celery is not installed."""
        def delay_func(*args, **kwargs):
            raise ImportError("Celery is not installed; falling back to FastAPI BackgroundTasks")
        func.delay = delay_func
        return func


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"planning", "planned", "failed"},
    "planning": {"planned", "needs_user", "needs_clarification", "failed"},
    "needs_clarification": {"planning", "failed"},
    "planned": {"approved", "planning", "failed"},
    "approved": {"generating", "failed"},
    "generating": {"rendering", "repairing", "failed"},
    "rendering": {"repairing", "reviewing", "completed", "failed"},
    "repairing": {"rendering", "failed"},
    "reviewing": {"completed", "failed"},
    "editing": {"rendering", "failed", "completed"},
    "needs_user": {"planning", "approved", "failed"},
    "completed": {"planning", "generating", "rendering", "editing"},
    "failed": {"planning", "approved", "generating", "rendering", "editing"},
}


def validate_status_transition(current: str | None, target: str) -> bool:
    """Validate whether a project status transition is permitted."""
    if not current or current == target:
        return True
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    return target in allowed


def _get_db_session():
    """Create a database session for use in background tasks."""
    from app.database import SessionLocal
    return SessionLocal()


def _update_job(db, job_id: str | None, **kwargs):
    """Update a job record and push a live workspace event."""
    if not job_id:
        return
    from app.models.project import Job
    from app.services.job_events import publish_project_event

    job = db.query(Job).filter(Job.id == job_id).first()
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        publish_project_event(
            str(job.project_id),
            {
                "job_id": str(job.id),
                "job_type": job.job_type,
                "job_status": job.status,
            },
        )


def _parse_task_args(args):
    """Extract project_id and job_id cleanly whether called as (self, proj_id, job_id) or (proj_id, job_id)."""
    if len(args) >= 3:
        # Bound Celery task invocation: (self, project_id, job_id)
        return str(args[1]), str(args[2])
    elif len(args) == 2:
        # Direct / BackgroundTasks invocation: (project_id, job_id)
        return str(args[0]), str(args[1])
    elif len(args) == 1:
        return str(args[0]), None
    raise ValueError(f"Invalid positional arguments for task: {args}")


def _is_timeout_failure(result: dict) -> bool:
    """Return True when the runner failed because a command exceeded its wall-clock budget."""
    for error in result.get("errors") or []:
        if not isinstance(error, dict):
            continue
        if error.get("timeout"):
            return True
        text = str(error.get("error") or "").lower()
        if "process timed out" in text or "timed out" in text:
            return True
    return False


def _schedule_pending_guidance_followup(db, project, project_id: str) -> str | None:
    """Consume queued guidance and dispatch an automatic follow-up job."""
    from app.models.project import Job
    from app.services.agent_runtime import (
        consume_pending_guidance,
        record_agent_action,
        record_project_message,
        set_agent_state,
    )

    pending = consume_pending_guidance(db, project)
    if not pending:
        return None

    instruction = "\n".join(
        item.get("content", "").strip()
        for item in pending
        if item.get("content")
    ).strip()
    if not instruction:
        return None

    job = Job(
        project_id=project_id,
        job_type="guidance",
        status="pending",
        progress=[{"step": "guidance", "status": "pending", "detail": instruction[:300]}],
    )
    db.add(job)
    project.status = "rendering"
    db.commit()
    db.refresh(job)
    set_agent_state(db, project, "editing", "Applying queued mid-job guidance")
    record_agent_action(
        db,
        project,
        "guidance",
        "started",
        "Applying queued mid-job guidance",
        {"instruction": instruction},
        job_id=str(job.id),
    )
    record_project_message(
        db,
        project,
        "assistant",
        f"Applying your queued guidance now: {instruction}",
        metadata={"job_id": str(job.id), "action": "apply_guidance"},
    )

    if settings.task_backend.lower() == "celery":
        run_guidance_followup.delay(str(project_id), str(job.id), instruction=instruction)
    elif settings.task_backend.lower() == "background":
        run_guidance_followup(str(project_id), str(job.id), instruction=instruction)
    else:
        raise ValueError(f"Unsupported task backend: {settings.task_backend}")
    return str(job.id)


@task_decorator
def run_planning(*args, **kwargs):
    """Generate an analysis plan via LLM."""
    project_id, job_id = _parse_task_args(args)

    db = _get_db_session()
    try:
        from app.models.project import Job, Project, UploadedFile
        from app.services.agent_runtime import (
            record_agent_action,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.planner import generate_plan

        _update_job(db, job_id, status="running", progress=[{"step": "planning", "status": "running"}])

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        files = db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()
        file_summaries = [f.file_summary for f in files if f.file_summary]

        # Combine first-class pasted guidance with any uploaded analysis plan files.
        custom_plan_parts = []
        if project.custom_plan_text and project.custom_plan_text.strip():
            custom_plan_parts.append(project.custom_plan_text.strip())
        for f in files:
            if f.file_role == "analysis_plan" and f.file_path:
                try:
                    custom_plan_parts.append(Path(f.file_path).read_text(errors="replace"))
                except Exception as ex:
                    logger.warning("Failed to read analysis plan file %s: %s", f.file_path, ex)
        custom_plan_text = "\n\n".join(part for part in custom_plan_parts if part.strip()) or None

        # Run the async planner in a sync context
        from app.schemas.schemas import ClarificationAnswer, ClarificationRequest

        stored_clarifications = (project.agent_memory or {}).get("clarifications") or []
        clarifications = [
            ClarificationAnswer(**item)
            for item in stored_clarifications
            if isinstance(item, dict)
        ]

        loop = asyncio.new_event_loop()
        try:
            plan = loop.run_until_complete(generate_plan(
                question=project.question or "",
                file_summaries=file_summaries,
                notes=project.notes,
                custom_plan_text=custom_plan_text,
                study_manifest=project.study_manifest,
                clarifications=clarifications,
            ))
        finally:
            loop.close()

        if isinstance(plan, ClarificationRequest):
            agent_memory = dict(project.agent_memory or {})
            agent_memory["pending_clarifications"] = plan.model_dump()
            project.agent_memory = agent_memory
            project.status = "needs_clarification"
            project.analysis_plan = None
            db.commit()
            set_agent_state(db, project, "needs_user", plan.message)
            record_agent_action(db, project, "plan", "clarification_needed", plan.message, job_id=job_id)
            _update_job(db, job_id, status="completed", progress=[{"step": "planning", "status": "clarification_needed"}])
            return {"status": "clarification_needed", "clarification": plan.model_dump()}

        # Save plan to project
        project.analysis_plan = plan.model_dump()
        manifest_ready = (project.study_manifest or {}).get("status") == "ready"
        manifest_domain = (project.study_manifest or {}).get("domain")
        auto_build = bool(
            project.auto_build
            and manifest_ready
            and manifest_domain in {"microbiome", "metabolomics"}
            and plan.domain == manifest_domain
            and plan.grouping_variable
            and any(step.enabled for step in plan.workflow)
        )
        project.status = "approved" if auto_build else "planned"
        db.commit()

        jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
        refresh_project_memory(db, project, files=files, jobs=jobs)
        _update_job(db, job_id, status="completed", progress=[{"step": "planning", "status": "completed"}])

        if not auto_build:
            set_agent_state(db, project, "needs_user", "Analysis plan ready for review")
            record_agent_action(db, project, "plan", "completed", "Analysis plan ready for review", job_id=job_id)
            return {"status": "completed", "plan": plan.model_dump(), "auto_build": False}

        record_agent_action(
            db,
            project,
            "plan",
            "approved",
            f"Validated inputs; automatically approved plan using {plan.grouping_variable}",
            {"grouping_variable": plan.grouping_variable, "group_levels": plan.group_levels},
            job_id=job_id,
        )
        generation_job = Job(project_id=project_id, job_type="generate", status="pending")
        db.add(generation_job)
        project.status = "generating"
        db.commit()
        db.refresh(generation_job)
        set_agent_state(db, project, "generating", "Generating validated analysis report")
        record_agent_action(
            db,
            project,
            "generate",
            "started",
            "Generating validated analysis report",
            job_id=str(generation_job.id),
        )

        try:
            if settings.task_backend.lower() == "celery":
                run_generation.delay(str(project_id), str(generation_job.id))
            elif settings.task_backend.lower() == "background":
                run_generation(str(project_id), str(generation_job.id))
            else:
                raise ValueError(f"Unsupported task backend: {settings.task_backend}")
        except Exception as exc:
            generation_job.status = "failed"
            generation_job.error = f"Failed to enqueue generation: {exc}"
            project.status = "failed"
            db.commit()
            set_agent_state(db, project, "failed", "Generation dispatch failed")
            record_agent_action(
                db,
                project,
                "generate",
                "failed",
                generation_job.error,
                job_id=str(generation_job.id),
            )
            raise

        return {
            "status": "completed",
            "plan": plan.model_dump(),
            "auto_build": True,
            "generation_job_id": str(generation_job.id),
        }

    except Exception as e:
        logger.exception("Planning failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(e))
        try:
            p = db.query(Project).filter(Project.id == project_id).first()
            if p:
                from app.services.agent_runtime import record_agent_action, set_agent_state
                p.status = "failed"
                db.commit()
                set_agent_state(db, p, "failed", "Planning failed")
                record_agent_action(db, p, "plan", "failed", str(e), job_id=job_id)
        except Exception:
            pass
        raise
    finally:
        db.close()


@task_decorator
def run_generation(*args, **kwargs):
    """Generate the Quarto project via LLM."""
    project_id, job_id = _parse_task_args(args)
    target_recipe_id = kwargs.get("target_recipe_id")

    db = _get_db_session()
    try:
        from app.models.project import Job, Project, UploadedFile
        from app.schemas.schemas import AnalysisPlan
        from app.services.agent_runtime import record_agent_action, refresh_project_memory, set_agent_state
        from app.services.generator import generate_project

        _update_job(db, job_id, status="running", progress=[{"step": "generation", "status": "running"}])

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.analysis_plan:
            raise ValueError(f"Project {project_id} has no approved plan")

        plan = AnalysisPlan(**project.analysis_plan)

        files = db.query(UploadedFile).filter(UploadedFile.project_id == project_id).all()
        file_summaries = [f.file_summary for f in files if f.file_summary]
        uploaded_paths: dict[str, list[str]] = {}
        for file_record in files:
            if file_record.file_role and file_record.file_path:
                uploaded_paths.setdefault(file_record.file_role, []).append(file_record.file_path)

        # Create project directory
        project_dir = Path(settings.projects_dir) / str(project.id)
        project_dir.mkdir(parents=True, exist_ok=True)
        project.project_dir = str(project_dir)
        db.commit()
        refresh_project_memory(db, project, files=files)

        # Copy uploaded files to project/data/
        data_dir = project_dir / "data"
        data_dir.mkdir(exist_ok=True)
        for file_record in files:
            if file_record.file_path:
                src = Path(file_record.file_path)
                dst = data_dir / src.name
                if src.exists() and not dst.exists():
                    shutil.copy2(str(src), str(dst))

        # Track progress
        progress_log = []

        def progress_callback(step_id: str, status: str, metadata: dict | None = None):
            entry = {"step": step_id, "status": status, "time": datetime.now(timezone.utc).isoformat()}
            if metadata:
                entry.update(metadata)

            existing = next((item for item in reversed(progress_log) if item["step"] == step_id), None)
            if existing:
                existing.update(entry)
            else:
                progress_log.append(entry)

            _update_job(db, job_id, progress=progress_log)

        # Run the generator
        loop = asyncio.new_event_loop()
        generated = loop.run_until_complete(generate_project(
            project_dir=str(project_dir),
            plan=plan,
            file_summaries=file_summaries,
            uploaded_file_paths=uploaded_paths,
            study_manifest=project.study_manifest,
            progress_callback=progress_callback,
        ))
        loop.close()

        project.status = "generated"
        db.commit()

        generated_relative_paths = [str(Path(path).resolve().relative_to(project_dir.resolve())) for path in generated if Path(path).exists()]
        refresh_project_memory(db, project, files=files)
        record_agent_action(
            db,
            project,
            "generate",
            "completed",
            f"Generated {len(generated_relative_paths)} project files",
            files=generated_relative_paths,
            job_id=job_id,
        )

        _update_job(db, job_id, status="completed", progress=progress_log)

        render_job = Job(
            project_id=project_id,
            job_type="recipe" if target_recipe_id else "render",
            status="pending",
            progress=[{"target_recipe_id": target_recipe_id}] if target_recipe_id else None,
        )
        db.add(render_job)
        project.status = "rendering"
        db.commit()
        db.refresh(render_job)
        set_agent_state(db, project, "rendering", "Rendering report")
        record_agent_action(db, project, "render", "started", "Rendering report", job_id=str(render_job.id))

        try:
            next_task = run_recipe_execution if target_recipe_id else run_rendering
            if settings.task_backend.lower() == "celery":
                if target_recipe_id:
                    next_task.delay(
                        str(project_id),
                        str(render_job.id),
                        recipe_id=target_recipe_id,
                    )
                else:
                    next_task.delay(str(project_id), str(render_job.id))
            elif settings.task_backend.lower() == "background":
                if target_recipe_id:
                    next_task(
                        str(project_id),
                        str(render_job.id),
                        recipe_id=target_recipe_id,
                    )
                else:
                    next_task(str(project_id), str(render_job.id))
            else:
                raise ValueError(f"Unsupported task backend: {settings.task_backend}")
        except Exception as exc:
            logger.exception("Failed to enqueue render task for project %s", project_id)
            render_job.status = "failed"
            render_job.error = f"Failed to enqueue render task: {exc}"
            project.status = "failed"
            db.commit()
            set_agent_state(db, project, "failed", "Render dispatch failed")
            record_agent_action(db, project, "render", "failed", f"Failed to enqueue render task: {exc}", job_id=str(render_job.id))
            return {
                "status": "completed",
                "files": generated,
                "render_job_id": str(render_job.id),
                "render_status": "failed",
            }

        return {"status": "completed", "files": generated, "render_job_id": str(render_job.id)}

    except Exception as e:
        logger.exception("Generation failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(e))
        try:
            p = db.query(Project).filter(Project.id == project_id).first()
            if p:
                from app.services.agent_runtime import record_agent_action, set_agent_state
                p.status = "failed"
                db.commit()
                set_agent_state(db, p, "failed", "Generation failed")
                record_agent_action(db, p, "generate", "failed", str(e), job_id=job_id)
        except Exception:
            pass
        raise
    finally:
        db.close()


@task_decorator
def run_rendering(*args, **kwargs):
    """Execute quarto render on the generated project."""
    project_id, job_id = _parse_task_args(args)

    db = _get_db_session()
    try:
        from app.models.project import Job, Project
        from app.services.agent_runtime import (
            record_agent_action,
            record_project_message,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.repair import repair_generated_project
        from app.services.reviewer import review_render_output
        from app.services.runner import run_project

        _update_job(db, job_id, status="running", progress=[{"step": "rendering", "status": "running"}])

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.project_dir:
            raise ValueError(f"Project {project_id} has no generated project directory")

        set_agent_state(db, project, "rendering", "Rendering report")

        # Track progress
        progress_log = []
        render_logs = []

        def progress_callback(step_id: str, status: str, line: str):
            if line:
                render_logs.append(line)

            entry = {"step": step_id, "status": status, "time": datetime.now(timezone.utc).isoformat()}
            if status in {"completed", "failed", "warning"} and line:
                entry["detail"] = line.splitlines()[0]

            existing = next((item for item in reversed(progress_log) if item["step"] == step_id), None)
            if existing:
                existing.update(entry)
                if status == "running":
                    existing.pop("detail", None)
            else:
                progress_log.append(entry)

            _update_job(db, job_id, progress=progress_log, logs="\n".join(render_logs[-200:]))

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_project(
                project_dir=project.project_dir,
                progress_callback=progress_callback,
            ))

            MAX_REPAIR_ATTEMPTS = 3
            repair_history = []
            repair_pass = 0

            while result["status"] != "completed" and repair_pass < MAX_REPAIR_ATTEMPTS:
                if _is_timeout_failure(result):
                    timeout_message = (
                        "Rendering stopped because a page exceeded its time budget while computing. "
                        "This is usually a long model fit, not a broken source file, so AI repair was skipped."
                    )
                    record_agent_action(
                        db,
                        project,
                        "repair",
                        "skipped",
                        timeout_message,
                        {"errors": result.get("errors"), "failed_page": result.get("failed_page")},
                        job_id=job_id,
                    )
                    progress_callback("repair", "failed", timeout_message)
                    record_project_message(
                        db,
                        project,
                        "assistant",
                        timeout_message,
                        metadata={
                            "job_id": job_id,
                            "status": "failed",
                            "failed_page": result.get("failed_page"),
                            "reason": "timeout",
                        },
                    )
                    break

                repair_pass += 1
                record_agent_action(db, project, "render", "failed", f"Render failed before repair pass #{repair_pass}", {"errors": result.get("errors")}, job_id=job_id)
                set_agent_state(db, project, "repairing", f"Repairing generated source after render failure (pass #{repair_pass})")
                progress_callback("repair", "running", f"Render failed. Starting AI repair pass #{repair_pass} of {MAX_REPAIR_ATTEMPTS}...")

                repair_result = loop.run_until_complete(repair_generated_project(
                    project.project_dir,
                    result,
                    repair_history=repair_history,
                ))
                repair_history.append({"pass": repair_pass, "failure": result.get("errors"), "repair": repair_result})
                result["repair"] = repair_result

                if repair_result.get("status") == "repaired":
                    repaired_paths = ", ".join(item["path"] for item in repair_result.get("repairs", []))
                    record_agent_action(
                        db,
                        project,
                        "repair",
                        "completed",
                        f"Applied repair pass #{repair_pass} to: {repaired_paths}",
                        files=[item["path"] for item in repair_result.get("repairs", [])],
                        job_id=job_id,
                    )
                    set_agent_state(db, project, "rendering", f"Rerendering after repair pass #{repair_pass}")
                    progress_callback("repair", "completed", f"Applied repair pass #{repair_pass} to: {repaired_paths}")
                    progress_callback("rerender", "running", f"Rerendering after repair pass #{repair_pass}...")
                    repaired_files = [item.get("path", "") for item in repair_result.get("repairs", [])]
                    rerun_data = any(Path(file_path).name == "data.R" for file_path in repaired_files)
                    result = loop.run_until_complete(run_project(
                        project_dir=project.project_dir,
                        progress_callback=progress_callback,
                        start_page=result.get("failed_page"),
                        run_data=rerun_data,
                    ))
                    result["repair"] = repair_result
                    progress_callback(
                        "rerender",
                        "completed" if result["status"] == "completed" else "failed",
                        f"Rerender pass #{repair_pass} completed" if result["status"] == "completed" else f"Rerender failed after repair pass #{repair_pass}",
                    )
                else:
                    repair_reason = repair_result.get("reason", "AI repair did not produce safe edits")
                    record_agent_action(db, project, "repair", "failed", repair_reason, job_id=job_id)
                    progress_callback("repair", "failed", repair_reason)
                    break
        finally:
            loop.close()

        if result["status"] == "completed":
            set_agent_state(db, project, "reviewing", "Reviewing rendered report")
            review_result = review_render_output(project.project_dir)
            result["review"] = review_result
            record_agent_action(
                db,
                project,
                "review",
                review_result["status"],
                review_result["summary"],
                {"checks": review_result.get("checks", [])},
                job_id=job_id,
            )
            if review_result["status"] == "failed":
                result["status"] = "failed"
                project.status = "failed"
                db.commit()
                jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
                refresh_project_memory(db, project, jobs=jobs)
                set_agent_state(db, project, "failed", review_result["summary"])
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"The report rendered, but final validation failed: {review_result['summary']}",
                    metadata={"job_id": job_id, "status": "failed"},
                )
            else:
                project.status = "completed"
                db.commit()
                jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
                refresh_project_memory(db, project, jobs=jobs)
                set_agent_state(db, project, "completed", "Report rendered and reviewed successfully")
                record_agent_action(db, project, "render", "completed", "Report rendered successfully", job_id=job_id)
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"The configured analysis finished successfully. {review_result['summary']}.",
                    metadata={
                        "job_id": job_id,
                        "status": "completed",
                        "review_status": review_result["status"],
                    },
                )
                _schedule_pending_guidance_followup(db, project, project_id)
        else:
            project.status = "failed"
            db.commit()
            jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
            refresh_project_memory(db, project, jobs=jobs)
            set_agent_state(db, project, "failed", "Render failed after repair attempt")
            record_project_message(
                db,
                project,
                "assistant",
                "The analysis did not render successfully after the repair attempts. I preserved the failure details for inspection.",
                metadata={"job_id": job_id, "status": "failed"},
            )

        db.commit()

        final_status = "completed" if result["status"] == "completed" else "failed"
        _update_job(
            db, job_id,
            status=final_status,
            progress=progress_log,
            logs="\n".join(render_logs),
            error=json.dumps(result.get("errors")) if result.get("errors") else None,
        )
        return result

    except Exception as e:
        logger.exception("Rendering failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(e))
        try:
            p = db.query(Project).filter(Project.id == project_id).first()
            if p:
                from app.services.agent_runtime import record_agent_action, set_agent_state
                p.status = "failed"
                db.commit()
                set_agent_state(db, p, "failed", "Rendering failed")
                record_agent_action(db, p, "render", "failed", str(e), job_id=job_id)
        except Exception:
            pass
        raise
    finally:
        db.close()


@task_decorator
def run_recipe_execution(*args, **kwargs):
    """Execute one recipe and only its stale dependency closure."""
    project_id, job_id = _parse_task_args(args)
    recipe_id = str(kwargs.get("recipe_id") or "")

    db = _get_db_session()
    try:
        from app.models.project import Job, Project
        from app.services.agent_runtime import (
            record_agent_action,
            record_project_message,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.recipe_execution import invalidate_recipe_cache, run_recipe_target
        from app.services.repair import repair_generated_project
        from app.services.reviewer import review_render_output

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.project_dir:
            raise ValueError(f"Project {project_id} has no generated project directory")
        if not recipe_id:
            raise ValueError("Targeted recipe execution requires recipe_id")

        progress_log = []
        execution_logs = []

        def progress_callback(step_id: str, status: str, line: str):
            if line:
                execution_logs.append(line)
            entry = {
                "step": step_id,
                "status": status,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            if line:
                entry["detail"] = line.splitlines()[0]
            existing = next(
                (item for item in reversed(progress_log) if item["step"] == step_id),
                None,
            )
            if existing:
                existing.update(entry)
            else:
                progress_log.append(entry)
            _update_job(
                db,
                job_id,
                status="running",
                progress=progress_log,
                logs="\n".join(execution_logs[-200:]),
            )

        set_agent_state(db, project, "rendering", f"Running targeted recipe {recipe_id}")
        record_agent_action(
            db,
            project,
            "recipe",
            "started",
            f"Running targeted recipe {recipe_id}",
            {"recipe_id": recipe_id},
            job_id=job_id,
        )

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            run_recipe_target(
                project.project_dir,
                recipe_id,
                progress_callback=progress_callback,
            )
        )
        repair_history = []
        repair_pass = 0
        while result["status"] != "completed" and repair_pass < 3:
            if _is_timeout_failure(result):
                progress_callback(
                    "repair",
                    "failed",
                    "Skipped AI repair because the recipe hit a computation timeout.",
                )
                break
            repair_pass += 1
            set_agent_state(
                db,
                project,
                "repairing",
                f"Repairing targeted recipe {recipe_id} (pass #{repair_pass})",
            )
            repair_result = loop.run_until_complete(
                repair_generated_project(
                    project.project_dir,
                    result,
                    repair_history=repair_history,
                )
            )
            repair_history.append(
                {
                    "pass": repair_pass,
                    "failure": result.get("errors"),
                    "repair": repair_result,
                }
            )
            if repair_result.get("status") != "repaired":
                break
            result = loop.run_until_complete(
                run_recipe_target(
                    project.project_dir,
                    recipe_id,
                    progress_callback=progress_callback,
                )
            )
        loop.close()

        if result["status"] == "completed":
            review = review_render_output(project.project_dir)
            result["review"] = review
            if review["status"] == "failed":
                result["status"] = "failed"
                error = review["summary"]
            else:
                error = None
                project.status = "completed"
                set_agent_state(db, project, "completed", f"Targeted recipe {recipe_id} is current")
                executed = result.get("executed_recipes") or []
                cache_hits = result.get("cache_hits") or []
                summary = (
                    f"Updated {recipe_id}. Executed {len(executed)} recipe node(s); "
                    f"reused {len(cache_hits)} cached node(s)."
                )
                record_agent_action(
                    db,
                    project,
                    "recipe",
                    "completed",
                    summary,
                    {
                        "recipe_id": recipe_id,
                        "executed_recipes": executed,
                        "cache_hits": cache_hits,
                    },
                    job_id=job_id,
                )
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"{summary} {review['summary']}.",
                    metadata={
                        "job_id": job_id,
                        "recipe_id": recipe_id,
                        "executed_recipes": executed,
                        "cache_hits": cache_hits,
                    },
                )
                _schedule_pending_guidance_followup(db, project, project_id)
        else:
            error = json.dumps(result.get("errors") or [])

        if result["status"] != "completed":
            invalidate_recipe_cache(project.project_dir, recipe_id)
            project.status = "failed"
            set_agent_state(db, project, "failed", f"Targeted recipe {recipe_id} failed")
            record_agent_action(
                db,
                project,
                "recipe",
                "failed",
                f"Targeted recipe {recipe_id} failed",
                {"errors": result.get("errors")},
                job_id=job_id,
            )
            record_project_message(
                db,
                project,
                "assistant",
                f"The targeted {recipe_id} run failed after repair attempts. Failure details are available in the job log.",
                metadata={"job_id": job_id, "recipe_id": recipe_id, "status": "failed"},
            )

        db.commit()
        jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
        refresh_project_memory(db, project, jobs=jobs)
        _update_job(
            db,
            job_id,
            status="completed" if result["status"] == "completed" else "failed",
            progress=progress_log,
            logs="\n".join(execution_logs),
            error=error,
        )
        return result
    except Exception as exc:
        logger.exception("Targeted recipe execution failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(exc))
        raise
    finally:
        db.close()


@task_decorator
def run_editing(*args, **kwargs):
    """Execute AI editing on project source code followed by re-rendering."""
    project_id, job_id = _parse_task_args(args)
    instruction = kwargs.get("instruction") or ""

    db = _get_db_session()
    try:
        from app.models.project import Job, Project
        from app.services.agent_runtime import (
            record_agent_action,
            record_project_message,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.editor import edit_generated_project
        from app.services.repair import repair_generated_project
        from app.services.reviewer import review_render_output
        from app.services.runner import run_project

        _update_job(db, job_id, status="running", progress=[{"step": "editing", "status": "running", "detail": instruction}])

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.project_dir:
            raise ValueError(f"Project {project_id} has no project directory")

        set_agent_state(db, project, "editing", "Editing generated source", {"instruction": instruction})

        loop = asyncio.new_event_loop()
        edit_result = loop.run_until_complete(edit_generated_project(project.project_dir, instruction))

        if edit_result.get("status") != "completed":
            reason = edit_result.get("reason", "AI editing failed to modify files.")
            _update_job(db, job_id, status="failed", error=reason)
            project.status = "failed"
            db.commit()
            set_agent_state(db, project, "failed", "AI edit failed")
            record_agent_action(db, project, "edit", "failed", reason, {"instruction": instruction}, job_id=job_id)
            record_project_message(
                db,
                project,
                "assistant",
                f"I could not apply the requested edit safely: {reason}",
                metadata={"job_id": job_id, "status": "failed"},
            )
            return edit_result

        modified_files = edit_result.get("modified_files") or []
        record_agent_action(
            db,
            project,
            "edit",
            "completed",
            edit_result.get("summary", "Updated generated source"),
            {
                "instruction": instruction,
                "apply_results": edit_result.get("apply_results") or [],
            },
            files=modified_files,
            job_id=job_id,
        )
        refresh_project_memory(db, project)
        set_agent_state(db, project, "rendering", "Rendering after edit")

        # Re-render after editing
        _update_job(db, job_id, status="running", progress=[
            {"step": "editing", "status": "completed", "detail": edit_result.get("summary", "Updated code"), "path": ", ".join(modified_files)},
            {"step": "rendering", "status": "running", "detail": "Re-rendering Quarto site..."}
        ])

        render_result = loop.run_until_complete(run_project(project.project_dir))
        repair_history = []
        repair_pass = 0
        max_repair_attempts = 3
        while render_result["status"] != "completed" and repair_pass < max_repair_attempts:
            if _is_timeout_failure(render_result):
                timeout_message = (
                    "The edited report hit a computation time budget. "
                    "AI repair was skipped because this is a runtime timeout, not a source syntax error."
                )
                record_agent_action(
                    db,
                    project,
                    "repair",
                    "skipped",
                    timeout_message,
                    {"errors": render_result.get("errors")},
                    job_id=job_id,
                )
                record_project_message(
                    db,
                    project,
                    "assistant",
                    timeout_message,
                    metadata={"job_id": job_id, "status": "failed", "reason": "timeout"},
                )
                break
            repair_pass += 1
            set_agent_state(
                db,
                project,
                "repairing",
                f"Repairing the requested edit (pass #{repair_pass})",
            )
            repair_result = loop.run_until_complete(
                repair_generated_project(
                    project.project_dir,
                    render_result,
                    repair_history=repair_history,
                )
            )
            repair_history.append(
                {
                    "pass": repair_pass,
                    "failure": render_result.get("errors"),
                    "repair": repair_result,
                }
            )
            if repair_result.get("status") != "repaired":
                break
            repaired_files = [item.get("path", "") for item in repair_result.get("repairs", [])]
            record_agent_action(
                db,
                project,
                "repair",
                "completed",
                f"Repaired the requested edit on pass #{repair_pass}",
                files=repaired_files,
                job_id=job_id,
            )
            set_agent_state(db, project, "rendering", f"Verifying edit repair pass #{repair_pass}")
            render_result = loop.run_until_complete(
                run_project(
                    project.project_dir,
                    start_page=render_result.get("failed_page"),
                    run_data=any(Path(path).name == "data.R" for path in repaired_files),
                )
            )
        loop.close()

        if render_result["status"] == "completed":
            set_agent_state(db, project, "reviewing", "Reviewing rerendered report")
            review_result = review_render_output(project.project_dir)
            render_result["review"] = review_result
            record_agent_action(
                db,
                project,
                "review",
                review_result["status"],
                review_result["summary"],
                {"checks": review_result.get("checks", [])},
                job_id=job_id,
            )
            if review_result["status"] == "failed":
                project.status = "failed"
                db.commit()
                set_agent_state(db, project, "failed", review_result["summary"])
                _update_job(db, job_id, status="failed", error=review_result["summary"])
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"I applied the requested edit and rendered the report, but final review failed: {review_result['summary']}",
                    metadata={"job_id": job_id, "status": "failed"},
                )
            else:
                project.status = "completed"
                db.commit()
                jobs = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).all()
                refresh_project_memory(db, project, jobs=jobs)
                set_agent_state(db, project, "completed", "Edit applied and report reviewed")
                record_agent_action(db, project, "render", "completed", "Report rerendered after edit", job_id=job_id)
                record_project_message(
                    db,
                    project,
                    "assistant",
                    (
                        f"Done — {edit_result.get('summary', 'the requested workspace change was applied')}. "
                        f"The report rerendered successfully and the review finished with status: {review_result['status']}."
                    ),
                    metadata={
                        "job_id": job_id,
                        "modified_files": modified_files,
                        "review_status": review_result["status"],
                    },
                )
                _schedule_pending_guidance_followup(db, project, project_id)
                _update_job(db, job_id, status="completed", progress=[
                    {"step": "editing", "status": "completed", "detail": edit_result.get("summary")},
                    {"step": "rendering", "status": "completed", "detail": "Quarto re-render succeeded"},
                    {"step": "review", "status": review_result["status"], "detail": review_result["summary"]}
                ])
        else:
            project.status = "failed"
            db.commit()
            set_agent_state(db, project, "failed", "Rerender failed after edit")
            record_agent_action(db, project, "render", "failed", "Re-rendering failed after applying edits.", {"errors": render_result.get("errors")}, job_id=job_id)
            record_project_message(
                db,
                project,
                "assistant",
                "I applied the requested edit, but the report still failed after the repair attempts. Review the latest job error before continuing.",
                metadata={"job_id": job_id, "status": "failed"},
            )
            _update_job(db, job_id, status="failed", error="Re-rendering failed after applying edits.")

        db.commit()
        return render_result

    except Exception as e:
        logger.exception("Editing task failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(e))
        try:
            p = db.query(Project).filter(Project.id == project_id).first()
            if p:
                from app.services.agent_runtime import record_agent_action, set_agent_state
                p.status = "failed"
                db.commit()
                set_agent_state(db, p, "failed", "Editing task failed")
                record_agent_action(db, p, "edit", "failed", str(e), {"instruction": instruction}, job_id=job_id)
        except Exception:
            pass
        raise
    finally:
        db.close()



@task_decorator
def run_guidance_followup(*args, **kwargs):
    """Interpret and apply queued mid-job guidance after a successful run."""
    project_id, job_id = _parse_task_args(args)
    instruction = str(kwargs.get("instruction") or "").strip()

    db = _get_db_session()
    try:
        from app.models.project import Job, Project
        from app.services.agent_runtime import (
            record_agent_action,
            record_project_message,
            refresh_project_memory,
            set_agent_state,
        )
        from app.services.analysis_configuration import apply_analysis_configuration
        from app.services.guidance_followup import decide_guidance_action

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")
        if not instruction:
            raise ValueError("Guidance follow-up requires an instruction")

        _update_job(
            db,
            job_id,
            status="running",
            progress=[{"step": "guidance", "status": "running", "detail": instruction[:300]}],
        )
        set_agent_state(db, project, "editing", "Interpreting queued guidance")

        loop = asyncio.new_event_loop()
        decision = loop.run_until_complete(decide_guidance_action(project, instruction))
        loop.close()

        action = decision.get("action") if decision.get("type") == "action" else None
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        message = str(decision.get("message") or "Applying queued guidance.")

        if action in {
            "set_recipe_enabled",
            "update_recipe_parameters",
            "set_analysis_variables",
            "rollback_analysis_configuration",
        }:
            mutation = apply_analysis_configuration(project, action, arguments)
            project.analysis_plan = mutation["plan"]
            db.commit()
            refresh_project_memory(db, project)
            record_agent_action(
                db,
                project,
                "analysis_config",
                "completed",
                mutation["summary"],
                {"operation": action, "arguments": arguments, "source": "queued_guidance"},
                job_id=job_id,
            )
            target_recipe_id = None
            if action == "update_recipe_parameters" and arguments.get("recipe_id"):
                target_recipe_id = str(arguments["recipe_id"])
            elif action == "set_recipe_enabled" and arguments.get("enabled") is True and arguments.get("recipe_id"):
                target_recipe_id = str(arguments["recipe_id"])

            follow_job = Job(
                project_id=project_id,
                job_type="recipe" if target_recipe_id else "generate",
                status="pending",
                progress=[{"target_recipe_id": target_recipe_id}] if target_recipe_id else None,
            )
            db.add(follow_job)
            project.status = "generating"
            db.commit()
            db.refresh(follow_job)
            record_project_message(
                db,
                project,
                "assistant",
                f"{mutation['summary']}. Rebuilding from your queued guidance now.",
                metadata={"job_id": str(follow_job.id), "source": "queued_guidance"},
            )
            if target_recipe_id:
                if settings.task_backend.lower() == "celery":
                    run_generation.delay(str(project_id), str(follow_job.id), target_recipe_id=target_recipe_id)
                else:
                    run_generation(str(project_id), str(follow_job.id), target_recipe_id=target_recipe_id)
            else:
                if settings.task_backend.lower() == "celery":
                    run_generation.delay(str(project_id), str(follow_job.id))
                else:
                    run_generation(str(project_id), str(follow_job.id))
            _update_job(db, job_id, status="completed", progress=[
                {"step": "guidance", "status": "completed", "detail": mutation["summary"]}
            ])
            return {"status": "completed", "delegated": "generate", "summary": mutation["summary"]}

        if action == "run_recipe":
            recipe_id = str(arguments.get("recipe_id") or "")
            if not recipe_id:
                raise ValueError("run_recipe guidance requires recipe_id")
            follow_job = Job(
                project_id=project_id,
                job_type="recipe",
                status="pending",
                progress=[{"target_recipe_id": recipe_id}],
            )
            db.add(follow_job)
            project.status = "rendering"
            db.commit()
            db.refresh(follow_job)
            record_project_message(
                db,
                project,
                "assistant",
                f"Running {recipe_id} from your queued guidance.",
                metadata={"job_id": str(follow_job.id), "recipe_id": recipe_id},
            )
            if settings.task_backend.lower() == "celery":
                run_recipe_execution.delay(str(project_id), str(follow_job.id), recipe_id=recipe_id)
            else:
                run_recipe_execution(str(project_id), str(follow_job.id), recipe_id=recipe_id)
            _update_job(db, job_id, status="completed", progress=[
                {"step": "guidance", "status": "completed", "detail": f"Delegated {recipe_id}"}
            ])
            return {"status": "completed", "delegated": "recipe", "recipe_id": recipe_id}

        if action == "edit_project" or decision.get("type") == "action":
            edit_instruction = str(decision.get("instruction") or instruction).strip()
            follow_job = Job(project_id=project_id, job_type="edit", status="pending")
            db.add(follow_job)
            project.status = "rendering"
            db.commit()
            db.refresh(follow_job)
            record_project_message(
                db,
                project,
                "assistant",
                message or "Applying your queued guidance as a verified workspace edit.",
                metadata={"job_id": str(follow_job.id), "source": "queued_guidance"},
            )
            if settings.task_backend.lower() == "celery":
                run_editing.delay(str(project_id), str(follow_job.id), instruction=edit_instruction)
            else:
                run_editing(str(project_id), str(follow_job.id), instruction=edit_instruction)
            _update_job(db, job_id, status="completed", progress=[
                {"step": "guidance", "status": "completed", "detail": "Delegated edit"}
            ])
            return {"status": "completed", "delegated": "edit"}

        project.status = "completed"
        db.commit()
        set_agent_state(db, project, "completed", "Queued guidance did not require changes")
        record_project_message(
            db,
            project,
            "assistant",
            str(decision.get("message") or "Queued guidance did not require further changes."),
            metadata={"job_id": job_id, "source": "queued_guidance"},
        )
        _update_job(db, job_id, status="completed", progress=[
            {"step": "guidance", "status": "completed", "detail": "No-op"}
        ])
        return {"status": "completed", "delegated": None}
    except Exception as exc:
        logger.exception("Guidance follow-up failed for project %s", project_id)
        _update_job(db, job_id, status="failed", error=str(exc))
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                from app.services.agent_runtime import record_agent_action, record_project_message, set_agent_state

                project.status = "failed"
                db.commit()
                set_agent_state(db, project, "failed", "Queued guidance failed")
                record_agent_action(db, project, "guidance", "failed", str(exc), job_id=job_id)
                record_project_message(
                    db,
                    project,
                    "assistant",
                    f"I could not apply the queued guidance automatically: {exc}",
                    metadata={"job_id": job_id, "status": "failed"},
                )
        except Exception:
            pass
        raise
    finally:
        db.close()




@task_decorator
def sync_bioc_knowledge(*args):
    """Synchronise the curated QMD Bioconductor knowledge catalog."""
    if not settings.bioc_knowledge_sync_enabled:
        return {"status": "disabled"}
    from app.database import SessionLocal
    from app.services.bioc_knowledge import sync_catalog

    db = SessionLocal()
    try:
        return sync_catalog(
            db,
            settings.bioc_knowledge_catalog_path,
            storage_root=settings.bioc_knowledge_storage_dir,
            channels=("stable", "preview") if settings.bioc_knowledge_sync_preview_enabled else ("stable",),
        )
    finally:
        db.close()
