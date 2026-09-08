from pathlib import Path
from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import HTMLResponse, Response
from omicsbase_shared.workspace import _get_project_dir, _resolve_safe_path, _build_file_tree

from omicsbase_shared.access import authorize_project

router = APIRouter(dependencies=[Depends(authorize_project)])

@router.get('/api/omicsbase/editor/{conversation_id}')
async def get_editor_page(conversation_id: str):
    html_path = Path(__file__).with_name('editor.html')
    if html_path.is_file():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'), status_code=200)
    return HTMLResponse(content='<h1>Editor template not found</h1>', status_code=500)


@router.get('/api/omicsbase/editor/{conversation_id}/tree')
async def get_editor_tree(conversation_id: str):
    pdir = _get_project_dir(conversation_id)
    return _build_file_tree(pdir)


@router.get('/api/omicsbase/editor/{conversation_id}/file')
async def get_editor_file(conversation_id: str, path: str):
    pdir = _get_project_dir(conversation_id)
    target = _resolve_safe_path(pdir, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail='File not found')

    ext = target.suffix.lower()
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.svg'):
        content_bytes = target.read_bytes()
        media_types = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
            '.pdf': 'application/pdf'
        }
        return Response(content=content_bytes, media_type=media_types.get(ext, 'application/octet-stream'))

    try:
        content = target.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        content = target.read_text(encoding='latin-1', errors='replace')
    return {'path': path, 'content': content, 'size': target.stat().st_size}


class SaveEditorFileRequest(BaseModel):
    content: str


@router.post('/api/omicsbase/editor/{conversation_id}/file')
async def save_editor_file(conversation_id: str, path: str, data: SaveEditorFileRequest):
    pdir = _get_project_dir(conversation_id)
    target = _resolve_safe_path(pdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + '.tmp')
    temp_target.write_text(data.content, encoding='utf-8')
    temp_target.replace(target)
    return {'status': 'ok', 'path': path, 'size': target.stat().st_size}

