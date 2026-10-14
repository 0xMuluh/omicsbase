"""OmicsBase Gateway for OpenHands v1.x (Agent Canvas + Agent Server).

Main application entrypoint configuring CORS, static asset mounting,
and modular routers for sessions, proxying, Monaco editor, and reports.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gateway.core import ASSETS_DIR
from gateway.sessions import router as sessions_router
from gateway.proxy import router as proxy_router
from gateway.editor import router as editor_router
from gateway.report import router as report_router

app = FastAPI(title="OmicsBase OpenHands Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static assets for custom Monaco editor
if ASSETS_DIR.is_dir():
    app.mount("/api/omicsbase/assets", StaticFiles(directory=str(ASSETS_DIR)), name="editor-assets")


@app.get("/api/omicsbase/health")
async def health():
    return {"status": "ok", "service": "omicsbase-gateway"}


# Include modular routers
app.include_router(sessions_router)
app.include_router(proxy_router)
app.include_router(editor_router)
app.include_router(report_router)
