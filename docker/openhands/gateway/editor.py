"""Custom Monaco editor endpoints, file tree generation, and VSCode route interception."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from gateway.core import _get_project_dir, _resolve_safe_path
from gateway.auth import SaveEditorFileRequest

router = APIRouter(tags=["editor"])


def _build_file_tree(base_dir: Path, current_dir: Optional[Path] = None) -> list[dict]:
    if current_dir is None:
        current_dir = base_dir
    items = []
    try:
        entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return items

    for entry in entries:
        if entry.name in (".git", "__pycache__", ".pytest_cache", ".quarto", "_site"):
            continue
        rel_path = str(entry.relative_to(base_dir))
        if entry.is_dir():
            items.append({
                "name": entry.name,
                "path": rel_path,
                "type": "directory",
                "children": _build_file_tree(base_dir, entry)
            })
        else:
            try:
                size = entry.stat().st_size
            except Exception:
                size = 0
            items.append({
                "name": entry.name,
                "path": rel_path,
                "type": "file",
                "size": size
            })
    return items


@router.api_route("/api/omicsbase/editor/{conversation_id}", methods=["GET", "HEAD"])
async def get_editor_page(conversation_id: str):
    html_path = Path(__file__).parent / "editor.html"
    if html_path.is_file():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"), status_code=200)
    return HTMLResponse(content="<h1>Editor template not found</h1>", status_code=500)


@router.api_route("/api/omicsbase/editor/{conversation_id}/tree", methods=["GET", "HEAD"])
async def get_editor_tree(conversation_id: str):
    pdir = _get_project_dir(conversation_id)
    return _build_file_tree(pdir)


@router.api_route("/api/omicsbase/editor/{conversation_id}/file", methods=["GET", "HEAD"])
async def get_editor_file(conversation_id: str, path: str):
    pdir = _get_project_dir(conversation_id)
    target = _resolve_safe_path(pdir, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = target.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".svg"):
        content_bytes = target.read_bytes()
        media_types = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".pdf": "application/pdf"
        }
        return Response(content=content_bytes, media_type=media_types.get(ext, "application/octet-stream"))

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_text(encoding="latin-1", errors="replace")
    return {"path": path, "content": content, "size": target.stat().st_size}


@router.post("/api/omicsbase/editor/{conversation_id}/file")
async def save_editor_file(conversation_id: str, path: str, data: SaveEditorFileRequest):
    pdir = _get_project_dir(conversation_id)
    target = _resolve_safe_path(pdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + ".tmp")
    temp_target.write_text(data.content, encoding="utf-8")
    temp_target.replace(target)
    return {"status": "ok", "path": path, "size": target.stat().st_size}


@router.get("/api/vscode/status")
async def get_vscode_status():
    return {"running": True, "enabled": True}


@router.get("/api/vscode/url")
async def get_vscode_url(
    base_url: Optional[str] = None,
    workspace_dir: Optional[str] = None,
):
    return {"url": "/api/omicsbase/editor/default"}


@router.get("/vscode")
@router.get("/vscode/{full_path:path}")
async def vscode_redirect(full_path: str = ""):
    return RedirectResponse(url="/api/omicsbase/editor/default")
