from pathlib import Path
from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse, HTMLResponse
from omicsbase_shared.workspace import _get_project_dir, _resolve_safe_path
from omicsbase_shared.reporting import _inspect_project_report

router = APIRouter()

@router.get('/api/omicsbase/report/{conversation_id}')
@router.get('/api/omicsbase/report/{conversation_id}/')
async def get_report_page(conversation_id: str):
    html_path = Path(__file__).with_name('report.html')
    if html_path.is_file():
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
            'Pragma': 'no-cache',
            'Expires': '0',
        }
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'), status_code=200, headers=headers)
    return HTMLResponse(content='<h1>Report template not found</h1>', status_code=500)


@router.get('/api/omicsbase/report/{conversation_id}/status')
async def get_report_status(conversation_id: str):
    pdir = _get_project_dir(conversation_id)
    report = _inspect_project_report(pdir)
    is_running = False
    agent_state_val = None
    try:
        from openhands.server.services.conversation_service import conversation_manager
        from openhands.core.schema.agent import AgentState
        agent_session = conversation_manager.get_agent_session(conversation_id)
        if agent_session:
            state = agent_session.get_state()
            if state is not None:
                agent_state_val = state.value if hasattr(state, 'value') else str(state)
                is_running = (state == AgentState.RUNNING)
    except Exception:
        pass
    report["is_agent_running"] = bool(is_running)
    report["agent_state"] = agent_state_val
    return report


@router.get('/api/omicsbase/report/{conversation_id}/site/{path:path}')
@router.get('/api/omicsbase/report/{conversation_id}/file/{path:path}')
async def get_report_site_file(conversation_id: str, path: str):
    pdir = _get_project_dir(conversation_id)
    site_dir = pdir / '_site'
    try:
        target = _resolve_safe_path(site_dir, path)
        if target.is_file():
            return FileResponse(str(target))
    except Exception:
        pass

    try:
        target = _resolve_safe_path(pdir, path)
        if target.is_file():
            return FileResponse(str(target))
    except Exception:
        pass

    raise HTTPException(status_code=404, detail='File not found')

