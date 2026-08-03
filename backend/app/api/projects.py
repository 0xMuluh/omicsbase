"""Projects API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.projects_agent import router as agent_router
from app.api.projects_crud import router as crud_router
from app.api.projects_files import router as files_router
from app.api.projects_pipeline import router as pipeline_router
from app.api.projects_notes import router as notes_router
from app.api.projects_note_executions import router as note_execution_router

router = APIRouter(prefix="/api/projects", tags=["projects"])

router.include_router(crud_router)
router.include_router(files_router)
router.include_router(pipeline_router)
router.include_router(notes_router)
router.include_router(note_execution_router)
router.include_router(agent_router)
