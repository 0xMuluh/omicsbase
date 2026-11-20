"""Core configuration, filesystem paths, and base helpers for OmicsBase Gateway."""
import os
import re
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
# In a user's own Reports job on Roihu the gateway serves one person: each report is a
# folder under PROJECTS_ROOT, and every request must carry the job's session key.
ROIHU = os.environ.get("OMICSBASE_GATEWAY_MODE") == "roihu"
# OmicsBase report (project) ids are MongoDB object ids.
PROJECT_ID = re.compile(r"^[a-f0-9]{24}$")

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


def _registry_entry(conversation_id: str) -> dict:
    session_file = SESSION_REGISTRY_DIR / f"{conversation_id}.json"
    if session_file.is_file():
        try:
            return json.loads(session_file.read_text())
        except Exception:
            pass
    return {}


def _get_conversation_owner(conversation_id: str) -> Optional[str]:
    if not conversation_id or conversation_id == "default":
        return None
    owner = _registry_entry(conversation_id).get("user_id")
    return str(owner) if owner else None


def _get_project_dir(conversation_id: str, user_id: Optional[str] = None) -> Path:
    """The report folder for a conversation or report id.

    On Roihu every report is a folder under PROJECTS_ROOT. When one gateway serves
    several users, only the caller's own folders (PROJECTS_ROOT/users/<user_id>) are
    searched; with no match the caller's most recent report is used, never another
    user's.
    """
    if ROIHU:
        root = PROJECTS_ROOT
    elif user_id:
        root = PROJECTS_ROOT / "users" / str(user_id)
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    base = root.resolve()

    if conversation_id and conversation_id != "default":
        candidates = [Path(_registry_entry(conversation_id).get("project_dir") or root), root / conversation_id]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved != base and resolved.is_relative_to(base) and resolved.is_dir():
                return candidate

    reports = [p for p in root.glob("*") if p.is_dir()]
    if reports:
        return max(reports, key=lambda p: p.stat().st_mtime)
    return root


def _resolve_safe_path(base_dir: Path, rel_path: str) -> Path:
    clean_rel = rel_path.lstrip("/").replace("\\", "/")
    target = (base_dir / clean_rel).resolve()
    base_resolved = base_dir.resolve()
    if not target.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail="Access denied: path traversal")
    return target
