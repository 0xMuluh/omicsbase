from pathlib import Path
from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

router = APIRouter()
ASSET_DIRECTORY = Path(__file__).with_name('assets')
ASSETS = {'editor.css', 'editor.js', 'report.css', 'report.js', 'report-prism.js'}


@router.get('/api/omicsbase/assets/{name}')
async def workspace_asset(name: str):
    if name not in ASSETS:
        raise HTTPException(404, 'Asset not found')
    return FileResponse(ASSET_DIRECTORY / name, headers={'Cache-Control': 'no-cache'})
