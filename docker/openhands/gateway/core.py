"""Core configuration, filesystem paths, and base helpers for OmicsBase Gateway."""
import os
import json
import logging
from pathlib import Path
from typing import Optional
from fastapi import HTTPException

logging.basicConfig(level=logging.INFO, format="[omicsbase-gateway] %(levelname)s: %(message)s")
logger = logging.getLogger("omicsbase_gateway")

AUTH_SECRET = os.environ.get("OMICSBASE_AUTH_SECRET", "")
AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "http://127.0.0.1:18000")
PROJECTS_ROOT = Path(os.environ.get("PROJECTS_ROOT", "/projects"))
SESSION_REGISTRY_DIR = Path("/home/openhands/.openhands/omicsbase_sessions")
CONVERSATIONS_DIR = Path(os.environ.get("OH_CONVERSATIONS_PATH", "/home/openhands/.openhands/agent-canvas/conversations"))
ASSETS_DIR = Path(__file__).parent / "assets"

# Normalize Google/Gemini API key environment variables for ACP agents & LiteLLM
if os.environ.get("GOOGLE_KEY"):
    if not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_KEY"]
    if not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GOOGLE_KEY"]



def get_session_api_key() -> str:
    key = os.environ.get("LOCAL_BACKEND_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if not key:
        key_file = Path("/home/openhands/.openhands/agent-canvas/api-key.txt")
        if key_file.exists():
            key = key_file.read_text().strip()
    return key


def _get_conversation_owner(conversation_id: str) -> Optional[str]:
    if not conversation_id or conversation_id == "default":
        return None
    session_file = SESSION_REGISTRY_DIR / f"{conversation_id}.json"
    if session_file.is_file():
        try:
            data = json.loads(session_file.read_text())
            return str(data.get("user_id")) if data.get("user_id") else None
        except Exception:
            pass
    return None


def _get_project_dir(conversation_id: str) -> Path:
    if conversation_id and conversation_id != "default":
        session_file = SESSION_REGISTRY_DIR / f"{conversation_id}.json"
        if session_file.is_file():
            try:
                data = json.loads(session_file.read_text())
                pdir = Path(data.get("project_dir", ""))
                if pdir.is_dir():
                    return pdir
            except Exception:
                pass
        for user_dir in (PROJECTS_ROOT / "users").glob("*"):
            if (user_dir / conversation_id).is_dir():
                return user_dir / conversation_id
        if (PROJECTS_ROOT / conversation_id).is_dir():
            return PROJECTS_ROOT / conversation_id

    # Fallback to most recently updated project
    user_projects = list((PROJECTS_ROOT / "users").glob("*/*"))
    if user_projects:
        user_projects.sort(key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)
        for p in user_projects:
            if p.is_dir():
                return p

    return PROJECTS_ROOT


def _resolve_safe_path(base_dir: Path, rel_path: str) -> Path:
    clean_rel = rel_path.lstrip("/").replace("\\", "/")
    target = (base_dir / clean_rel).resolve()
    base_resolved = base_dir.resolve()
    if not target.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail="Access denied: path traversal")
    return target
