"""Quarto report inspection, live status polling, and compiled site file delivery."""
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
import httpx
import yaml

from gateway.core import _get_project_dir, _resolve_safe_path, get_session_api_key, AGENT_SERVER_URL

router = APIRouter(tags=["report"])


def _inspect_project_report(pdir: Path) -> dict:
    project_title = "Analysis Pipeline"
    ordered_chapters = []

    quarto_yml = pdir / "_quarto.yml"
    if quarto_yml.is_file():
        try:
            cfg = yaml.safe_load(quarto_yml.read_text(encoding="utf-8")) or {}
            btitle = cfg.get("book", {}).get("title") or cfg.get("website", {}).get("title") or cfg.get("project", {}).get("title")
            if btitle:
                project_title = btitle

            def extract_items(node):
                if isinstance(node, list):
                    for item in node:
                        extract_items(item)
                elif isinstance(node, dict):
                    if "file" in node:
                        ordered_chapters.append({
                            "file": node["file"],
                            "title": node.get("title") or node.get("text")
                        })
                    elif "href" in node:
                        ordered_chapters.append({
                            "file": node["href"],
                            "title": node.get("text") or node.get("title")
                        })
                    elif "contents" in node:
                        extract_items(node["contents"])
                    elif "chapters" in node:
                        extract_items(node["chapters"])
                    else:
                        for k in ("sidebar", "navbar", "tools", "items"):
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

    if project_title == "Analysis Pipeline":
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
                if project_title != "Analysis Pipeline":
                    break
            except Exception:
                pass

    site_dir = pdir / "_site"
    has_site = (site_dir / "index.html").is_file()

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


@router.api_route("/api/omicsbase/report/{conversation_id}", methods=["GET", "HEAD"])
@router.api_route("/api/omicsbase/report/{conversation_id}/", methods=["GET", "HEAD"])
async def get_report_page(request: Request, conversation_id: str):
    html_path = Path(__file__).parent / "report.html"
    if html_path.is_file():
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        content = html_path.read_text(encoding="utf-8")
        theme = (request.query_params.get("theme") or request.cookies.get("omicsbase_theme") or "dark").lower()
        if theme == "light":
            content = content.replace('class="dark"', 'class="light"')
        return HTMLResponse(content=content, status_code=200, headers=headers)
    return HTMLResponse(content="<h1>Report template not found</h1>", status_code=500)


@router.api_route("/api/omicsbase/report/{conversation_id}/status", methods=["GET", "HEAD"])
async def get_report_status(conversation_id: str):
    pdir = _get_project_dir(conversation_id)
    report = _inspect_project_report(pdir)
    is_running = False
    agent_state_val = None
    try:
        api_key = get_session_api_key()
        headers = {"X-Session-API-Key": api_key}
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{AGENT_SERVER_URL}/api/conversations/{conversation_id}", headers=headers)
            if resp.status_code == 200:
                convo_info = resp.json()
                exec_status = convo_info.get("execution_status")
                agent_state_val = exec_status
                is_running = (exec_status == "running")
    except Exception:
        pass
    report["is_agent_running"] = bool(is_running)
    report["agent_state"] = agent_state_val
    return report


@router.api_route("/api/omicsbase/report/{conversation_id}/site/{path:path}", methods=["GET", "HEAD"])
@router.api_route("/api/omicsbase/report/{conversation_id}/file/{path:path}", methods=["GET", "HEAD"])
async def get_report_site_file(conversation_id: str, path: str):
    pdir = _get_project_dir(conversation_id)
    site_dir = pdir / "_site"
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

    raise HTTPException(status_code=404, detail="File not found")
