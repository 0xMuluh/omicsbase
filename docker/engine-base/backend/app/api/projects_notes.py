"""Compatibility aggregator for the project-scoped Notes routes.

The route implementations live in app.api.notes. This module keeps the
historical router and private import names available while callers migrate.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.notes.cells import router as note_cells_router
from app.api.notes.files import router as note_files_router
from app.api.notes.reports import router as note_reports_router
from app.api.notes.standalone import standalone_router
from app.api.notes.threads import router as note_threads_router
from app.api.notes.turns import note_agent_router, note_thread_turn
from app.services.note_payloads import (
    note_execution_observation_payload as _note_execution_observation_payload,
)
from app.services.note_promotion import promote_cell_to_workspace as _promote_cell_to_workspace
from app.services.note_turn_context import (
    build_note_agent_context as _note_agent_context,
    wait_for_note_execution as _wait_for_note_execution,
)

router = APIRouter()
router.include_router(note_threads_router)
router.include_router(note_files_router)
router.include_router(note_cells_router)
router.include_router(note_reports_router)

__all__ = [
    "router",
    "note_agent_router",
    "note_thread_turn",
    "standalone_router",
    "_note_execution_observation_payload",
    "_promote_cell_to_workspace",
    "_note_agent_context",
    "_wait_for_note_execution",
]
