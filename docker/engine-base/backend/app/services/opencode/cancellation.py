"""Cross-process cancellation for the headless OpenCode server.

The workspace API and the Celery worker are separate processes (and, in the
development compose file, separate containers).  Cancelling an asyncio task in
the API therefore cannot stop the OpenCode session owned by the worker.  This
module keeps the provider-side operation deliberately small: use OpenCode's
supported session abort endpoint, with a short bounded timeout, and let the
durable database state remain the source of truth.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def abort_session(
    session_id: str | None,
    project_dir: str | Path | None,
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    """Ask the configured OpenCode server to abort one active session.

    Returning ``False`` means that no usable provider session was available or
    the request failed.  The caller must still finalize durable cancellation;
    provider abort is a best-effort side effect, not a second source of truth.
    """
    value = str(session_id or "").strip()
    if not value.startswith("ses"):
        return False

    try:
        import httpx

        from app.services.opencode_requests import basic_auth
        from app.services.opencode_server import server_base_url

        directory = str(Path(project_dir).resolve()) if project_dir else None
        params = {"directory": directory} if directory else None
        with httpx.Client(
            base_url=server_base_url(),
            auth=basic_auth(),
            timeout=httpx.Timeout(
                connect=min(2.0, timeout_seconds),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=timeout_seconds,
            ),
        ) as client:
            response = client.post(f"/session/{value}/abort", params=params)
            if response.status_code < 400:
                return True
            logger.warning(
                "OpenCode session abort returned %s for %s: %s",
                response.status_code,
                value,
                response.text[:300],
            )
    except Exception as exc:
        logger.warning("OpenCode session abort failed for %s: %s", value, exc)
    return False


def revoke_celery_task(task_id: str | None) -> bool:
    """Terminate a known Celery task after provider abort was requested."""
    value = str(task_id or "").strip()
    if not value:
        return False
    try:
        from app.tasks.analysis import celery_app

        if celery_app is None:
            return False
        celery_app.control.revoke(value, terminate=True, signal="SIGTERM", reply=False)
        return True
    except Exception as exc:
        logger.warning("Celery revoke failed for %s: %s", value, exc)
        return False



def abort_agent_operation(run, project=None) -> dict[str, bool]:
    """Abort the provider and Celery side effects for one durable AgentRun.

    Durable state is deliberately handled by ``agent_runs.cancel_agent_run``;
    this helper only performs best-effort transport cleanup shared by both
    cancellation endpoints.
    """
    from app.config import settings

    metadata = run.run_metadata if isinstance(getattr(run, "run_metadata", None), dict) else {}
    project_id = str(getattr(run, "project_id", "") or "").strip()
    project_dir = None
    if project is not None:
        project_dir = getattr(project, "project_dir", None)
    if not project_dir and project_id:
        project_dir = Path(settings.projects_dir) / project_id
    backend = str(metadata.get("agent_backend") or "opencode").strip().lower()
    session_id = (
        str(metadata.get("agent_session_id") or "").strip() or None
        if backend == "opencode"
        else None
    )
    if backend == "opencode" and not session_id and project_dir is not None:
        session_file = Path(project_dir) / ".omicsbase" / "opencode_session"
        try:
            if session_file.is_file():
                session_id = session_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            logger.debug("Could not read OpenCode session marker %s", session_file, exc_info=True)
    task_id = str(metadata.get("celery_task_id") or "").strip() or None
    return {
        "session_aborted": abort_session(session_id, project_dir),
        "task_revoked": revoke_celery_task(task_id),
    }
__all__ = ["abort_session", "revoke_celery_task", "abort_agent_operation"]
