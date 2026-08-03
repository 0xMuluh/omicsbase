"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.files import router as files_router
from app.api.inline_edit import router as inline_edit_router
from app.api.projects_notes import note_agent_router, standalone_router as standalone_notes_router
from app.api.projects_note_executions import standalone_execution_router as standalone_note_execution_router
from app.api.knowledge import router as knowledge_router
from app.api.runs import router as runs_router
from app.api.input_contract import router as input_contract_router
from app.api.projects import router as projects_router
from app.config import settings
from app.middleware.auth import ApiKeyMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    # Ensure database tables exist
    from app.database import Base, engine
    import app.models.project  # noqa: F401
    import app.models.notes  # noqa: F401
    import app.models.knowledge  # noqa: F401
    import app.models.runs  # noqa: F401
    Base.metadata.create_all(bind=engine)

    from app.database import SessionLocal
    from app.services.agent_runs import pause_stale_agent_runs

    startup_db = SessionLocal()
    try:
        paused_runs = pause_stale_agent_runs(
            startup_db,
            stale_after_seconds=settings.agent_run_stale_after_seconds,
        )
        if paused_runs:
            print(f"! Paused {paused_runs} stale agent run(s) during startup")
    finally:
        startup_db.close()

    # Verify prerequisites
    from app.services.runner import check_prerequisites

    checks = await check_prerequisites()
    if checks.get("r_available"):
        print(f"✓ R available: {checks.get('r_version', 'unknown')}")
    else:
        print("✗ R not found — rendering will fail")

    if checks.get("quarto_available"):
        print(f"✓ Quarto available: {checks.get('quarto_version', 'unknown')}")
    else:
        print("✗ Quarto not found — rendering will fail")

    # Warm system prompt & registry cache
    from app.services.llm import load_system_prompt
    load_system_prompt()
    print("✓ System prompt & registry cache warmed")

    # Production auth guard
    if not settings.api_key and not settings.dev_mode:
        print(
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  CRITICAL SECURITY WARNING: No API key configured and      ║\n"
            "║  dev_mode is False. All endpoints are unauthenticated.     ║\n"
            "║  Set API_KEY in .env or enable DEV_MODE=true for local     ║\n"
            "║  development.                                              ║\n"
            "╚══════════════════════════════════════════════════════════════╝"
        )
    elif settings.dev_mode:
        print("⚠ Running in DEV_MODE — auth bypass, default tenants, and sandbox bypass are permitted")

    yield


app = FastAPI(
    title="OmicsBase",
    description="AI-powered microbiome analysis with transparent, reproducible Quarto reports.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(ApiKeyMiddleware)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(projects_router)
app.include_router(files_router)
app.include_router(inline_edit_router)
app.include_router(standalone_notes_router)
app.include_router(note_agent_router)
app.include_router(standalone_note_execution_router)
app.include_router(knowledge_router)
app.include_router(runs_router)
app.include_router(input_contract_router)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "omicsbase"}


@app.get("/api/prerequisites")
async def prerequisites():
    """Check system prerequisites (R, Quarto)."""
    from app.services.runner import check_prerequisites
    return await check_prerequisites()
