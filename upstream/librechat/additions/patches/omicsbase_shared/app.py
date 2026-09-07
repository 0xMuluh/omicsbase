import os
import uuid
from fastapi import HTTPException, Request
from pydantic import BaseModel

os.environ.setdefault(
    'OPENHANDS_CONVERSATION_VALIDATOR_CLS',
    'omicsbase_shared.validator.OmicsBaseConversationValidator',
)
from openhands.server.app import app as base_app
from openhands.server.user_auth.user_auth import get_user_auth
from openhands.server.services.conversation_service import create_new_conversation
from omicsbase_shared.identity import verify_ticket
from omicsbase_shared.registry import bind_project


class LaunchRequest(BaseModel):
    ticket: str


@base_app.post('/api/omicsbase/conversations')
async def launch(data: LaunchRequest, request: Request):
    auth = await get_user_auth(request)
    user_id = await auth.get_user_id()
    try:
        claims = verify_ticket(data.ticket)
        if claims['sub'] != user_id or claims.get('purpose') != 'launch':
            raise ValueError('Invalid launch ticket')
        conversation_id = uuid.uuid4().hex
        project_dir = bind_project(user_id, claims['project_id'], conversation_id)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(403, 'Invalid project authorization')
    secrets = await auth.get_user_secrets()
    info = await create_new_conversation(
        user_id=user_id,
        git_provider_tokens=await auth.get_provider_tokens(),
        custom_secrets=secrets.custom_secrets if secrets else None,
        selected_repository=None,
        selected_branch=None,
        initial_user_msg=None,
        image_urls=None,
        replay_json=None,
        conversation_id=conversation_id,
        conversation_instructions=(
            f'This conversation works in {project_dir}. Use that directory for all project work. '
            'The runtime and installed packages are shared with your other conversations; '
            'other project directories must only be changed when explicitly requested. '
            f'The OmicsBase project ID is {claims["project_id"]}. '
            'Run R analysis through Rscript in the terminal. Browser state is shared; open '
            'the intended URL before inspecting a page.'
        ),
    )
    return {'status': 'ok', 'conversation_id': conversation_id, 'conversation_status': info.status}


import json
from pathlib import Path
from omicsbase_shared.registry import state_root
from omicsbase_shared.identity import identifier, user_root, project_root

def _get_project_dir(conversation_id: str) -> Path:
    try:
        cid = identifier(conversation_id)
        path = state_root() / f'{cid}.json'
        if path.exists():
            entry = json.loads(path.read_text())
            user_id = entry.get('user_id')
            project_id = entry.get('project_id')
            if user_id and project_id:
                pdir = user_root(user_id) / project_id
                if pdir.is_dir():
                    return pdir
        default_path = project_root() / cid
        if default_path.is_dir():
            return default_path
    except Exception:
        pass
    return project_root()

def _resolve_safe_path(base_dir: Path, rel_path: str) -> Path:
    clean_rel = rel_path.lstrip('/').replace('\\', '/')
    target = (base_dir / clean_rel).resolve()
    base_resolved = base_dir.resolve()
    if not str(target).startswith(str(base_resolved)):
        raise HTTPException(status_code=403, detail='Access denied: path traversal')
    return target

def _build_file_tree(base_dir: Path, current_dir: Path = None) -> list[dict]:
    if current_dir is None:
        current_dir = base_dir
    items = []
    try:
        entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return items

    for entry in entries:
        if entry.name in ('.git', '__pycache__', '.pytest_cache', '.quarto', '_site'):
            continue
        rel_path = str(entry.relative_to(base_dir))
        if entry.is_dir():
            items.append({
                'name': entry.name,
                'path': rel_path,
                'type': 'directory',
                'children': _build_file_tree(base_dir, entry)
            })
        else:
            try:
                size = entry.stat().st_size
            except Exception:
                size = 0
            items.append({
                'name': entry.name,
                'path': rel_path,
                'type': 'file',
                'size': size
            })
    return items

@base_app.get('/api/omicsbase/editor/{conversation_id}')
async def get_editor_page(conversation_id: str):
    html_path = Path(__file__).with_name('editor.html')
    if html_path.is_file():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'), status_code=200)
    return HTMLResponse(content='<h1>Editor template not found</h1>', status_code=500)

@base_app.get('/api/omicsbase/editor/{conversation_id}/tree')
async def get_editor_tree(conversation_id: str):
    pdir = _get_project_dir(conversation_id)
    return _build_file_tree(pdir)

@base_app.get('/api/omicsbase/editor/{conversation_id}/file')
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

@base_app.post('/api/omicsbase/editor/{conversation_id}/file')
async def save_editor_file(conversation_id: str, path: str, data: SaveEditorFileRequest):
    pdir = _get_project_dir(conversation_id)
    target = _resolve_safe_path(pdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + '.tmp')
    temp_target.write_text(data.content, encoding='utf-8')
    temp_target.replace(target)
    return {'status': 'ok', 'path': path, 'size': target.stat().st_size}


# --- Live Progressive Report Engine ---
import time
import yaml


def _inspect_project_report(pdir: Path) -> dict:
    project_title = "Omics Analysis Pipeline"
    ordered_chapters = []
    quarto_yml = pdir / "_quarto.yml"
    if quarto_yml.is_file():
        try:
            with open(quarto_yml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            project_title = (
                cfg.get("website", {}).get("title")
                or cfg.get("book", {}).get("title")
                or cfg.get("title")
                or project_title
            )
            def extract_items(node):
                if isinstance(node, list):
                    for sub in node:
                        extract_items(sub)
                elif isinstance(node, dict):
                    href = node.get("href") or node.get("file")
                    text = node.get("text") or node.get("title")
                    if href and (href.endswith(".qmd") or href.endswith(".md")):
                        ordered_chapters.append({"file": href, "title": text})
                    for k in ("menu", "contents", "chapters", "left", "right"):
                        if k in node:
                            extract_items(node[k])
                elif isinstance(node, str) and (node.endswith(".qmd") or node.endswith(".md")):
                    ordered_chapters.append({"file": node, "title": None})

            extract_items(cfg.get("website", {}).get("navbar", {}))
            extract_items(cfg.get("website", {}).get("sidebar", {}))
            extract_items(cfg.get("book", {}).get("chapters", []))
            extract_items(cfg.get("chapters", []))
        except Exception:
            pass

    # If title not found from _quarto.yml, try markdown files (e.g. Statistical Analysis Plan or README)
    if project_title == "Omics Analysis Pipeline":
        for md_path in pdir.glob("*.md"):
            try:
                lines = md_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                for line in lines[:10]:
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        candidate = stripped[2:].strip()
                        if candidate and len(candidate) > 3:
                            project_title = candidate
                            break
                if project_title != "Omics Analysis Pipeline":
                    break
            except Exception:
                pass
        if project_title == "Omics Analysis Pipeline":
            for md_path in pdir.glob("*Analysis Plan*.md"):
                project_title = md_path.stem
                break

    site_dir = pdir / "_site"
    has_site = (site_dir / "index.html").is_file()

    # Discover all .qmd files recursively (including pages/ and subdirectories)
    all_qmd = {}
    for p in sorted(pdir.rglob("*.qmd")):
        if "_site" in p.parts or ".git" in p.parts:
            continue
        rel_str = str(p.relative_to(pdir))
        all_qmd[rel_str] = p

    seen = set()
    chapters = []

    for item in ordered_chapters:
        f_raw = item["file"]
        qpath = all_qmd.get(f_raw)
        if not qpath:
            for rel, p in all_qmd.items():
                if rel.endswith(f_raw) or p.name == f_raw:
                    qpath = p
                    break
        if qpath and qpath not in seen:
            seen.add(qpath)
            rel_str = str(qpath.relative_to(pdir))
            title = item.get("title")
            if not title:
                try:
                    txt = qpath.read_text(encoding="utf-8", errors="ignore")
                    if txt.startswith("---"):
                        parts = txt.split("---", 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1])
                            if isinstance(fm, dict):
                                title = fm.get("title")
                except Exception:
                    pass
            if not title:
                title = qpath.stem.replace("_", " ").title()

            rel_html = qpath.relative_to(pdir).with_suffix(".html")
            compiled = (site_dir / rel_html).is_file()
            if compiled:
                status = "completed"
            elif site_dir.is_dir() and (time.time() - site_dir.stat().st_mtime) < 30:
                status = "running"
            else:
                status = "queued"
            chapters.append({
                "name": rel_str,
                "title": title,
                "compiled": compiled,
                "status": status,
                "html_path": str(rel_html) if compiled else None,
                "type": "chapter"
            })

    # Any remaining .qmd files in project not in navbar/sidebar
    for rel_str, qpath in all_qmd.items():
        if qpath in seen:
            continue
        seen.add(qpath)
        title = None
        try:
            txt = qpath.read_text(encoding="utf-8", errors="ignore")
            if txt.startswith("---"):
                parts = txt.split("---", 2)
                if len(parts) >= 3:
                    fm = yaml.safe_load(parts[1])
                    if isinstance(fm, dict):
                        title = fm.get("title")
        except Exception:
            pass
        if not title:
            title = qpath.stem.replace("_", " ").title()
        rel_html = qpath.relative_to(pdir).with_suffix(".html")
        compiled = (site_dir / rel_html).is_file()
        if compiled:
            status = "completed"
        elif site_dir.is_dir() and (time.time() - site_dir.stat().st_mtime) < 30:
            status = "running"
        else:
            status = "queued"
        chapters.append({
            "name": rel_str,
            "title": title,
            "compiled": compiled,
            "status": status,
            "html_path": str(rel_html) if compiled else None,
            "type": "chapter"
        })

    # Discover R pipeline scripts alongside Quarto chapters
    scripts = []
    r_dir = pdir / "R"
    if r_dir.is_dir():
        for r_script in sorted(r_dir.glob("*.R")):
            stem = r_script.stem.replace("_", " ").title()
            rel_str = str(r_script.relative_to(pdir))
            log_file = pdir / "output" / f"{r_script.stem}.log"
            status = "queued"
            if log_file.is_file():
                try:
                    txt = log_file.read_text(errors="ignore")
                    if "Execution halted" in txt or "Error in " in txt or "Error: " in txt:
                        status = "failed"
                    elif (time.time() - log_file.stat().st_mtime) < 45:
                        status = "running"
                    else:
                        status = "completed"
                except Exception:
                    status = "completed"
            elif has_site:
                status = "completed"
            else:
                out_res = pdir / "output" / "results"
                if out_res.is_dir() and any(out_res.glob(f"*{r_script.stem}*.rds")):
                    status = "completed"
                elif out_res.is_dir() and any(out_res.glob("*.rds")):
                    status = "completed"

            scripts.append({
                "name": r_script.name,
                "title": stem,
                "path": rel_str,
                "status": status,
                "type": "script"
            })

    figures = []
    fig_dirs = [
        "results", "figures", "plots",
        "output/figures", "output/plots", "output/results", "output",
        "."
    ]
    for dname in fig_dirs:
        sub = pdir / dname
        if sub.is_dir():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.svg", "*.webp"):
                for f in sorted(sub.glob(ext)):
                    if f.name.startswith("."):
                        continue
                    rel = str(f.relative_to(pdir))
                    if not any(x["path"] == rel for x in figures):
                        figures.append({
                            "name": f.name,
                            "path": rel,
                            "size": f.stat().st_size
                        })

    tables = []
    tbl_dirs = [
        "results", "tables", "data",
        "output/results", "output/tables", "output",
        "."
    ]
    for dname in tbl_dirs:
        sub = pdir / dname
        if sub.is_dir():
            for ext in ("*.csv", "*.tsv", "*.xlsx", "*.rds"):
                for t in sorted(sub.glob(ext)):
                    if t.name.startswith("."):
                        continue
                    rel = str(t.relative_to(pdir))
                    if not any(x["path"] == rel for x in tables):
                        tables.append({
                            "name": t.name,
                            "path": rel,
                            "size": t.stat().st_size
                        })

    return {
        "project_title": project_title,
        "has_site": has_site,
        "chapters": chapters,
        "scripts": scripts,
        "figures": figures,
        "tables": tables
    }


@base_app.get('/api/omicsbase/report/{conversation_id}')
@base_app.get('/api/omicsbase/report/{conversation_id}/')
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


@base_app.get('/api/omicsbase/report/{conversation_id}/status')
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


@base_app.get('/api/omicsbase/report/{conversation_id}/site/{path:path}')
@base_app.get('/api/omicsbase/report/{conversation_id}/file/{path:path}')
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


# Inject custom styling and detector when embedded in LibreChat iframe
import glob
from pathlib import Path
import openhands.server.static
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.types import Scope

def _patch_openhands_frontend():
    try:
        for filepath in glob.glob('/app/frontend/build/assets/root-layout-*.js'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            target1 = '},[o?.status,o,d,t.pathname]),e.jsxs(e.Fragment,{children:[e.jsxs("aside"'
            repl1 = '},[o?.status,o,d,t.pathname]),(window.self!==window.top||window.location.search.includes("embedded=true"))?null:e.jsxs(e.Fragment,{children:[e.jsxs("aside"'
            target2 = 'className:A("h-screen lg:min-w-[1024px] flex flex-col md:flex-row bg-base",a==="/"?"p-0":"p-0 md:p-3 md:pl-0"'
            repl2 = 'className:A("h-screen flex flex-col md:flex-row bg-base",(window.self!==window.top||window.location.search.includes("embedded=true"))?"p-0 md:p-3":(a==="/"?"p-0":"p-0 md:p-3 md:pl-0")'
            changed = False
            if target1 in content:
                content = content.replace(target1, repl1)
                changed = True
            if target2 in content:
                content = content.replace(target2, repl2)
                changed = True
            if changed:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f'[OmicsBase] Patched {filepath} for embedded layout')
    except Exception as e:
        print(f'[OmicsBase] Frontend patch check warning: {e}')

    try:
        root = Path('/app/frontend/build')
        assets = root / 'assets'
        if not assets.is_dir():
            return
        repl = [
            ('3*1024*1024', '20*1024*1024'),
            ('Files exceeding 3MB', 'Files exceeding 20MB'),
            ('exceeding the 3MB limit', 'exceeding the 20MB limit'),
            # Toolbar tab ordering: served (Report) -> vscode (Editor) -> editor (Changes)
            ('p=[{isActive:c("editor"),icon:_0,onClick:()=>g("editor"),tooltipContent:u(q.COMMON$CHANGES),tooltipAriaLabel:u(q.COMMON$CHANGES)}', 'p=[{isActive:c("served"),icon:p0,onClick:()=>g("served"),tooltipContent:"Report",tooltipAriaLabel:"Report"},{isActive:c("vscode"),icon:m0,onClick:()=>g("vscode"),tooltipContent:"Editor",tooltipAriaLabel:"Editor"},{isActive:c("editor"),icon:_0,onClick:()=>g("editor"),tooltipContent:u(q.COMMON$CHANGES),tooltipAriaLabel:u(q.COMMON$CHANGES)}]'),
            ('p=[{isActive:c("vscode"),icon:m0,onClick:()=>g("vscode"),tooltipContent:"Editor",tooltipAriaLabel:"Editor"},{isActive:c("editor"),icon:_0,onClick:()=>g("editor"),tooltipContent:u(q.COMMON$CHANGES),tooltipAriaLabel:u(q.COMMON$CHANGES)},{isActive:c("served"),icon:p0,onClick:()=>g("served"),tooltipContent:u(q.COMMON$APP),tooltipAriaLabel:u(q.COMMON$APP)}]', 'p=[{isActive:c("served"),icon:p0,onClick:()=>g("served"),tooltipContent:"Report",tooltipAriaLabel:"Report"},{isActive:c("vscode"),icon:m0,onClick:()=>g("vscode"),tooltipContent:"Editor",tooltipAriaLabel:"Editor"},{isActive:c("editor"),icon:_0,onClick:()=>g("editor"),tooltipContent:u(q.COMMON$CHANGES),tooltipAriaLabel:u(q.COMMON$CHANGES)}]'),
            ('{isActive:c("vscode"),icon:m0,onClick:()=>g("vscode"),tooltipContent:h.jsx(C0,{}),tooltipAriaLabel:u(q.COMMON$CODE)},', '{isActive:c("vscode"),icon:m0,onClick:()=>g("vscode"),tooltipContent:"Editor",tooltipAriaLabel:"Editor"},'),
            ('{isActive:c("terminal"),icon:d0,onClick:()=>g("terminal"),tooltipContent:u(q.COMMON$TERMINAL),tooltipAriaLabel:u(q.COMMON$TERMINAL)},', ''),
            ('{isActive:c("jupyter"),icon:u0,onClick:()=>g("jupyter"),tooltipContent:u(q.COMMON$JUPYTER),tooltipAriaLabel:u(q.COMMON$JUPYTER)},', ''),
            (',{isActive:c("browser"),icon:f0,onClick:()=>g("browser"),tooltipContent:u(q.COMMON$BROWSER),tooltipAriaLabel:u(q.COMMON$BROWSER)}', ''),
            ('selectedTab:r,shouldShownAgentLoading:C}=st(),{t:S}=_e(),w=r==="editor"', 'selectedTab:__r,shouldShownAgentLoading:C}=st(),r=(__r==="vscode"?"vscode":(__r==="editor"?"editor":"served")),{t:S}=_e(),w=r==="editor"'),
            ('selectedTab:__r,shouldShownAgentLoading:C}=st(),r=(__r==="served"?"served":(__r==="editor"?"editor":"vscode")),{t:S}=_e(),w=r==="editor"', 'selectedTab:__r,shouldShownAgentLoading:C}=st(),r=(__r==="vscode"?"vscode":(__r==="editor"?"editor":"served")),{t:S}=_e(),w=r==="editor"'),
            ('r=(__r==="served"?"served":"editor")', 'r=(__r==="vscode"?"vscode":(__r==="editor"?"editor":"served"))'),
            ('r=(__r==="served"?"served":(__r==="editor"?"editor":"vscode"))', 'r=(__r==="vscode"?"vscode":(__r==="editor"?"editor":"served"))'),
            ('r=__r==="vscode"?"editor":__r', 'r=(__r==="vscode"?"vscode":(__r==="editor"?"editor":"served"))'),
            ('w(M),S(I)', 'w(M==="vscode"?"vscode":(M==="editor"?"editor":"served")),S(I)'),
            ('w(M==="served"?"served":"editor"),S(I)', 'w(M==="vscode"?"vscode":(M==="editor"?"editor":"served")),S(I)'),
            ('w(M==="served"?"served":(M==="editor"?"editor":"vscode")),S(I)', 'w(M==="vscode"?"vscode":(M==="editor"?"editor":"served")),S(I)'),
            ('w(M==="vscode"?"editor":M)', 'w(M==="vscode"?"vscode":(M==="editor"?"editor":"served"))'),
            ('[M,j]=Gs("conversation-selected-tab","editor")', '[M,j]=Gs("conversation-selected-tab","served")'),
            ('[M,j]=Gs("conversation-selected-tab","vscode")', '[M,j]=Gs("conversation-selected-tab","served")'),
            # Fix getWebHosts returning array vs dict
            ('Object.keys(a.data.hosts)', '(Array.isArray(a.data.hosts)?a.data.hosts:Object.keys(a.data.hosts))'),
            # Drawer tab title: Report / Editor
            ('I?S(q.COMMON$APP):o?S(q.COMMON$CODE):', 'I?"Report":o?"Editor":'),
            ('I?S(q.COMMON$APP):', 'I?"Report":'),
            ('I?"Live Report":o?"Editor":', 'I?"Report":o?"Editor":'),
            ('I?"Live Report":', 'I?"Report":'),
            ('tooltipContent:"Live Report"', 'tooltipContent:"Report"'),
            ('tooltipAriaLabel:"Live Report"', 'tooltipAriaLabel:"Report"'),
            ('r||n("editor")', 'r||n("served")'),
            ('[o?JSON.parse(o):t,a]', '[(()=>{try{return o?JSON.parse(o):t}catch{return t}})(),a]'),
            # Prevent right-panel lockout behind giant Loading... card while agent connects
            ('return C?h.jsx(wh,{}):', 'return false?h.jsx(wh,{}):'),
        ]
        renamed: dict[str, str] = {}

        def rewrite_refs():
            for ref in root.rglob('*'):
                if not ref.is_file() or ref.suffix not in {'.js', '.html', '.css', '.json', '.map'}:
                    continue
                try:
                    text = ref.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    continue
                original = text
                for old, new in renamed.items():
                    text = text.replace(old, new)
                if text != original:
                    ref.write_text(text, encoding='utf-8')
                    print(f'[OmicsBase] Updated refs in {ref.relative_to(root)}')

        def bust(path: Path) -> Path:
            if path.name.endswith('-u27.js'):
                return path
            stem = path.stem
            for prev in ('-u20', '-u21', '-u22', '-u23', '-u24', '-u25', '-u26'):
                if stem.endswith(prev):
                    stem = stem[:-len(prev)]
            new_path = path.with_name(stem + '-u27.js')
            if new_path.exists():
                return new_path
            path.replace(new_path)
            renamed[path.name] = new_path.name
            print(f'[OmicsBase] Cache-bust {path.name} -> {new_path.name}')
            return new_path

        for path in list(assets.glob('*.js')):
            text = path.read_text(encoding='utf-8', errors='ignore')
            if not any(a in text for a, _ in repl):
                continue
            for a, b in repl:
                text = text.replace(a, b)
            path.write_text(text, encoding='utf-8')
            print(f'[OmicsBase] Patched frontend in {path.name}')
            if path.name.startswith('conversation-'):
                bust(path)

        # Also bust the conversation route entry + module manifest (immutable-cached parents)
        for path in list(assets.glob('conversation-*.js')) + list(assets.glob('manifest-*.js')) + list(assets.glob('conversation-service.api-*.js')):
            if path.name.endswith('-u27.js'):
                continue
            bust(path)

        if renamed:
            rewrite_refs()
    except Exception as e:
        print(f'[OmicsBase] Frontend patch warning: {e}')


_patch_openhands_frontend()

_INDEX_HTML_CACHE = None

def _get_embedded_index_html(index_path: str) -> str:
    global _INDEX_HTML_CACHE
    if _INDEX_HTML_CACHE is None:
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                content = f.read()
            injection = (
                '<script id="omicsbase-embedded-detector">\n'
                '  (function() {\n'
                '    function applyEmbedded() {\n'
                '      try {\n'
                '        var isEmbedded = (window.self !== window.top) || (window.location.search.indexOf("embedded=true") !== -1);\n'
                '        if (isEmbedded) {\n'
                '          var styleId = "omicsbase-hide-sidebar-style";\n'
                '          var existing = document.getElementById(styleId);\n'
                '          if (!existing) {\n'
                '            var s = document.createElement("style");\n'
                '            s.id = styleId;\n'
                '            s.textContent = "div[data-testid=\\"root-layout\\"] > aside, aside:has(nav), aside.md\\\\:w-\\\\[75px\\\\] { display: none !important; } button[data-testid=\\"download-vscode-button\\"] { display: none !important; } div[data-testid=\\"root-layout\\"] { min-width: 0 !important; width: 100% !important; padding-left: 0.75rem !important; }";\n'
                '            (document.head || document.documentElement).appendChild(s);\n'
                '          }\n'
                '        }\n'
                '      } catch (e) {\n'
                '        var styleId = "omicsbase-hide-sidebar-style";\n'
                '        if (!document.getElementById(styleId)) {\n'
                '          var s = document.createElement("style");\n'
                '          s.id = styleId;\n'
                '          s.textContent = "div[data-testid=\\"root-layout\\"] > aside, aside:has(nav), aside.md\\\\:w-\\\\[75px\\\\] { display: none !important; } button[data-testid=\\"download-vscode-button\\"] { display: none !important; } div[data-testid=\\"root-layout\\"] { min-width: 0 !important; width: 100% !important; padding-left: 0.75rem !important; }";\n'
                '          (document.head || document.documentElement).appendChild(s);\n'
                '        }\n'
                '      }\n'
                '    }\n'
                '    applyEmbedded();\n'
                '    document.addEventListener("DOMContentLoaded", applyEmbedded);\n'
                '    window.addEventListener("load", applyEmbedded);\n'
                '    setInterval(applyEmbedded, 500);\n'
                '  })();\n'
                '</script>\n'
            )
            theme_bridge = Path(__file__).with_name('theme.js').read_text()
            injection += '<script id="omicsbase-theme-bridge">' + theme_bridge + '</script>'
            if '</head>' in content:
                _INDEX_HTML_CACHE = content.replace('</head>', injection + '</head>', 1)
            else:
                _INDEX_HTML_CACHE = injection + content
        except Exception:
            return None
    return _INDEX_HTML_CACHE


class OmicsBaseSPAStaticFiles(openhands.server.static.SPAStaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        if isinstance(resp, FileResponse) and resp.path.endswith('index.html'):
            modified_html = _get_embedded_index_html(resp.path)
            if modified_html:
                return HTMLResponse(content=modified_html, status_code=resp.status_code)
        # Patched hashed assets must not be treated as immutable forever.
        name = path.rsplit('/', 1)[-1]
        if name.endswith(('.js', '.css')) and (
            name.startswith('conversation-') or name.startswith('manifest-') or any(f'-u{i}.' in name for i in range(20, 30))
        ):
            try:
                resp.headers['cache-control'] = 'no-cache, must-revalidate'
            except Exception:
                pass
        return resp


openhands.server.static.SPAStaticFiles = OmicsBaseSPAStaticFiles

# Register integration routes before the frontend catch-all mount.
from openhands.server.listen import app

