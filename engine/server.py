import os
import asyncio
import fcntl
import urllib.request
from urllib.parse import quote
import json
from pathlib import Path
from starlette.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from engine.kernel.executor import execute_note_cell
from engine.knowledge.search import search_bioc_knowledge

PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/app/projects")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
INTERNAL_SECRET = os.environ.get("OMICSBASE_AUTH_SECRET", "")
LIBRECHAT_INTERNAL_URL = os.environ.get("LIBRECHAT_INTERNAL_URL", "http://api:3080").rstrip("/")

# New project files remain writable by the shared project group.
# prepare_runtime_dirs.sh assigns that group and setgid to existing directories.
os.umask(0o002)
os.makedirs(PROJECTS_DIR, exist_ok=True)

# Initialize MCP Server for NoteThreads
mcp = MCPServer(
    name="OmicsBaseNoteThreads",
    instructions="OmicsBase NoteThreads Execution Engine. Provides persistent, in-memory R execution, automatic plot generation, data frame formatting, and grounded Bioconductor knowledge retrieval for downstream bioinformatics."
)

@mcp.tool(
    name="execute_r_cell",
    description=(
        "Execute an R code cell in the thread's persistent R kernel. Variables, data objects, and loaded libraries stay in memory across calls. "
        "Automatically captures stdout, renders ggplot2 plots, and formats tables. "
        "If correcting, refining, or re-running a previous cell that failed or needs updating, pass its cell ID in 'cell_id' to update that cell in place rather than creating a duplicate."
    )
)
async def execute_r_cell(code: str, thread_id: str = "default", cell_id: str = None) -> str:
    """Persist the lifecycle around direct R execution without blocking the ASGI loop."""
    return await asyncio.to_thread(_execute_agent_cell, code, thread_id, cell_id)


def _persist_cell(thread_id, action, payload):
    req = urllib.request.Request(
        f"{LIBRECHAT_INTERNAL_URL}/api/notes/{quote(thread_id, safe='')}/internal/{action}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Internal-Secret": INTERNAL_SECRET},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _execute_agent_cell(code, thread_id, cell_id=None):
    saved = None
    if INTERNAL_SECRET and LIBRECHAT_INTERNAL_URL:
        try:
            payload = {"code": code, "timeout_seconds": 180}
            if cell_id:
                payload["cell_id"] = str(cell_id).strip()
            saved = _persist_cell(thread_id, "start", payload)
        except Exception as exc:
            return f"Could not save the code cell; R was not executed: {exc}"

    try:
        result = _run_cell(code, thread_id, 180, saved["executionId"] if saved else None)
    except Exception as exc:
        result = {"success": False, "error": str(exc), "markdown": f"Execution failed: {exc}"}
    status = ("cancelled" if result.get("cancelled") else "timed_out" if result.get("timed_out")
              else "failed" if result.get("success") is False else "completed")
    warning = ""
    if saved:
        try:
            _persist_cell(thread_id, f"finish/{saved['executionId']}", result)
        except Exception as exc:
            warning = f"\n\nResult could not be saved: {exc}. R has already run; do not rerun automatically."
        meta = (f"<!-- noteCell cellId={saved['cellId']} executionId={saved['executionId']} "
                f"status={status} -->\n")
    else:
        meta = ""
    return meta + result.get("markdown", "") + warning


def _cancel_path(thread_id, execution_id=None):
    if execution_id and (len(execution_id) != 24 or any(c not in "0123456789abcdef" for c in execution_id)):
        raise ValueError("Invalid execution ID")
    name = f"cancel-{execution_id}.flag" if execution_id else "cancel.flag"
    return os.path.join(PROJECTS_DIR, thread_id, ".omicsbase", "note-kernel", name)


def _run_cell(code, thread_id, timeout_seconds, execution_id=None):
    cancel_path = _cancel_path(thread_id, execution_id)
    if not execution_id:
        Path(cancel_path).unlink(missing_ok=True)
    lock_path = Path(cancel_path).parent / "execute.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if os.path.exists(cancel_path):
                return {"success": False, "cancelled": True, "error": "Cancelled",
                        "markdown": "Execution cancelled before starting."}
            return execute_note_cell(
                projects_dir=PROJECTS_DIR, thread_id=thread_id, code=code, base_url=BASE_URL,
                timeout_seconds=timeout_seconds, cancel_check=lambda: os.path.exists(cancel_path),
            )
    finally:
        Path(cancel_path).unlink(missing_ok=True)


@mcp.tool(
    name="search_bioc_books",
    description="Search the pinned Bioconductor QMD knowledge index (OSCA single-cell, OSTA spatial, OMA microbiome, Mass Spec, Metabonaut) for authoritative R code recipes, workflows, and statistical methodology. Use whenever asked how to perform omics tasks or when resolving package errors."
)
def search_bioc_books(query: str, book: str = "", limit: int = 4) -> str:
    """
    Search curated Bioconductor books for canonical R code snippets and workflow guidance.
    Optional books: 'osca', 'osta', 'oma', 'r-for-mass-spectrometry', 'metabonaut'.
    """
    book_filter = book.strip().lower() if book and book.strip() else None
    res = search_bioc_knowledge(query=query, book=book_filter, limit=limit)
    return res.get("markdown", "")

@mcp.tool(
    name="list_thread_files",
    description="List all files available in the current thread/study workspace (including uploaded data and outputs)."
)
def list_thread_files(thread_id: str = "default") -> list[str]:
    thread_dir = os.path.join(PROJECTS_DIR, thread_id)
    if not os.path.exists(thread_dir):
        return []
    files = []
    for root, _, filenames in os.walk(thread_dir):
        for f in filenames:
            rel = os.path.relpath(os.path.join(root, f), thread_dir)
            files.append(rel)
    return sorted(files)

@mcp.tool(
    name="render_quarto_report",
    description="Compile the Quarto website project into HTML for the given project_id. Returns compilation status, stdout, stderr, and the live preview URL."
)
def render_quarto_report(project_id: str = "default") -> str:
    """
    Compile the Quarto project at /app/projects/{project_id} into an HTML website.
    """
    import subprocess
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)
    qmd_files = [f for f in os.listdir(project_dir) if f.endswith(".qmd")]
    if not os.path.exists(os.path.join(project_dir, "_quarto.yml")) and len(qmd_files) == 0:
        _scaffold_default_quarto(project_dir, project_id)
    
    try:
        proc = subprocess.run(
            ["quarto", "render", project_dir],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=300
        )
        has_site = os.path.exists(os.path.join(project_dir, "_site", "index.html"))
        report_url = f"{BASE_URL}/projects/{project_id}/_site/index.html" if has_site else None
        if proc.returncode == 0 and has_site:
            return f"Quarto report rendered successfully!\nPreview URL: {report_url}"
        else:
            return f"Quarto compilation failed (Exit code {proc.returncode}):\n{proc.stderr}"
    except Exception as e:
        return f"Error rendering Quarto report: {e}"

def _scaffold_default_quarto(project_dir: str, project_id: str):
    quarto_yml = os.path.join(project_dir, "_quarto.yml")
    if not os.path.exists(quarto_yml):
        with open(quarto_yml, "w") as f:
            f.write(f"""project:
  type: website
  output-dir: _site

website:
  title: "OmicsBase Study: {project_id}"
  navbar:
    left:
      - href: index.qmd
        text: Overview

format:
  html:
    theme: cosmo
    toc: true
    code-fold: show
""")

    index_qmd = os.path.join(project_dir, "index.qmd")
    if not os.path.exists(index_qmd):
        with open(index_qmd, "w") as f:
            f.write(f"""---
title: "Downstream Omics Study Report"
subtitle: "Study: {project_id}"
date: today
format:
  html:
    toc: true
---

## Overview

This publication-grade report is generated and maintained by OmicsBase.

```{{r}}
#| echo: true
#| warning: false
suppressPackageStartupMessages({{
  library(ggplot2)
}})

cat("R and Bioconductor runtime initialized.\\n")
```
""")

# ASGI Starlette app for SSE + static hosting + direct execution
app = mcp.sse_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)

from engine.access import ProjectAccess
app.add_middleware(ProjectAccess)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("DOMAIN_CLIENT", "http://localhost:3080")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Server-side execute only (LibreChat control plane). Browser must not call this.
async def handle_execute(request: Request):
    if INTERNAL_SECRET:
        provided = request.headers.get("x-internal-secret") or request.headers.get("X-Internal-Secret")
        if provided != INTERNAL_SECRET:
            return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)

    code = data.get("code", "")
    thread_id = data.get("thread_id", "default")
    timeout_seconds = int(data.get("timeout_seconds") or 180)
    if not code.strip():
        return JSONResponse({"success": False, "error": "Code cannot be empty"}, status_code=400)

    result = await asyncio.to_thread(
        _run_cell, code, thread_id, timeout_seconds, data.get("execution_id"),
    )
    if result.get("cancelled"):
        result["success"] = False
    return JSONResponse(result)


async def handle_cancel(request: Request):
    if INTERNAL_SECRET:
        provided = request.headers.get("x-internal-secret") or request.headers.get("X-Internal-Secret")
        if provided != INTERNAL_SECRET:
            return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)
    thread_id = data.get("thread_id", "default")
    flag = Path(_cancel_path(thread_id, data.get("execution_id")))
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    return JSONResponse({"success": True, "thread_id": thread_id})


app.add_route("/api/execute", handle_execute, methods=["POST"])
app.add_route("/api/cancel", handle_cancel, methods=["POST"])

# Direct HTTP endpoint for frontend knowledge queries
async def handle_knowledge_search(request: Request):
    q = request.query_params.get("q", "")
    book = request.query_params.get("book", None)
    limit = int(request.query_params.get("limit", 4))
    res = search_bioc_knowledge(query=q, book=book, limit=limit)
    return JSONResponse(res)

app.add_route("/api/knowledge/search", handle_knowledge_search, methods=["GET"])

# Endpoint for Quarto compilation
async def handle_quarto_render(request: Request):
    import asyncio
    project_id = request.path_params.get("project_id", "default")
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)
    
    qmd_files = [f for f in os.listdir(project_dir) if f.endswith(".qmd")]
    if not os.path.exists(os.path.join(project_dir, "_quarto.yml")) and len(qmd_files) == 0:
        _scaffold_default_quarto(project_dir, project_id)
        
    proc = await asyncio.create_subprocess_exec(
        "quarto", "render", project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=project_dir
    )
    stdout, stderr = await proc.communicate()
    
    exit_code = proc.returncode
    site_dir = os.path.join(project_dir, "_site")
    has_site = os.path.exists(os.path.join(site_dir, "index.html"))
    report_url = f"{BASE_URL}/projects/{project_id}/_site/index.html" if has_site else None
    
    return JSONResponse({
        "success": exit_code == 0 and has_site,
        "exit_code": exit_code,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "report_url": report_url,
        "has_site": has_site
    })

app.add_route("/api/projects/{project_id}/render", handle_quarto_render, methods=["POST"])

# Endpoint for checking project status (report existence, files)
async def handle_project_status(request: Request):
    project_id = request.path_params.get("project_id", "default")
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    has_site = os.path.exists(os.path.join(project_dir, "_site", "index.html"))
    report_url = f"{BASE_URL}/projects/{project_id}/_site/index.html" if has_site else None
    
    files = []
    if os.path.exists(project_dir):
        for root, _, filenames in os.walk(project_dir):
            if "_site" in root or ".git" in root or ".omicsbase" in root:
                continue
            for f in filenames:
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                files.append(rel)
                
    return JSONResponse({
        "project_id": project_id,
        "has_site": has_site,
        "report_url": report_url,
        "files": sorted(files),
    })

app.add_route("/api/projects/{project_id}/status", handle_project_status, methods=["GET"])

# Serve generated plots, tables, and Quarto sites directly to the browser
app.mount("/projects", StaticFiles(directory=PROJECTS_DIR, html=True), name="projects")

if __name__ == "__main__":
    import uvicorn
    print("Starting NoteThreads MCP & Execution Server on 0.0.0.0:8001...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
