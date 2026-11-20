"""OmicsBase Gateway for OpenHands v1.x (Agent Canvas + Agent Server).

Main application entrypoint configuring CORS, static asset mounting,
and modular routers for sessions, proxying, Monaco editor, and reports.
"""
import hmac
import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gateway.auth import request_is_authorized
from gateway.core import ASSETS_DIR, ROIHU, get_session_api_key
from gateway.sessions import router as sessions_router
from gateway.proxy import router as proxy_router
from gateway.editor import router as editor_router
from gateway.report import router as report_router
from gateway.project import router as project_router

app = FastAPI(title="OmicsBase OpenHands Gateway", version="1.0.0")

# Credentialed cross-origin requests come only from the OmicsBase app (DOMAIN_CLIENT).
# On Roihu the job's session key guards every request and the app's origin is not set.
if ROIHU:
    origins = {"allow_origin_regex": r"https?://.*"}
else:
    origins = {"allow_origins": [o.strip().rstrip("/") for o in os.environ.get("DOMAIN_CLIENT", "").split(",") if o.strip()]}
app.add_middleware(
    CORSMiddleware,
    **origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if ROIHU:
    @app.middleware("http")
    async def require_session_key(request: Request, call_next):
        """Other users on the compute node can reach this port; only the owner's
        requests (Caddy adds the job's key) get through."""
        key = get_session_api_key()
        given = request.headers.get("X-Session-API-Key", "")
        if request.method != "OPTIONS" and request.url.path != "/api/omicsbase/health" and not (
            key and hmac.compare_digest(given.encode(), key.encode())
        ):
            return Response(status_code=401)
        return await call_next(request)


# Mount static assets for custom Monaco editor
if ASSETS_DIR.is_dir():
    app.mount("/api/omicsbase/assets", StaticFiles(directory=str(ASSETS_DIR)), name="editor-assets")


@app.get("/api/omicsbase/auth")
async def login_gate(request: Request):
    """Caddy forward_auth check for the public OpenHands host."""
    return Response(status_code=204 if request_is_authorized(request) else 401)


@app.get("/api/omicsbase/health")
async def health():
    return {"status": "ok", "service": "omicsbase-gateway"}


# Include modular routers
app.include_router(sessions_router)
app.include_router(proxy_router)
app.include_router(editor_router)
app.include_router(report_router)
if ROIHU:
    app.include_router(project_router)
