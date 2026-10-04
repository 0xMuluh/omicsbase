import os
import asyncio
import fcntl
import re
import subprocess
import threading
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
    instructions=(
        "OmicsBase NoteThreads: a persistent R/Bioconductor session per note, with the\n"
        "curated Bioconductor books and installed-package documentation.\n\n"
        "Before you write code:\n"
        "- For a workflow or method, call search_bioc_books.\n"
        "- For a package function you have not already run successfully in this note,\n"
        "  call r_help to get the installed version's usage and arguments.\n"
        "- To find example data, call list_datasets. Use real datasets only.\n\n"
        "When you run code (execute_r_cell):\n"
        "- One step per cell, a few lines.\n"
        "- Only use objects created by earlier successful cells in this note. If unsure,\n"
        "  check with ls() first.\n"
        "- Inspect before you transform: class(), dim(), names(colData()), assayNames(),\n"
        "  reducedDimNames(), rowData columns.\n"
        "- Keep data in its Bioconductor container (TreeSummarizedExperiment,\n"
        "  SingleCellExperiment, SpatialExperiment, MultiAssayExperiment) and use the\n"
        "  ecosystem's functions.\n"
        "- Plot with the ecosystem's plotting functions:\n"
        "  TreeSummarizedExperiment: miaViz (plotAbundance, plotBoxplot, plotPrevalence,\n"
        "  plotRowTree, plotSeries, plotDMNFit, plotRDA, plotLoadings);\n"
        "  reduced dimensions and ordinations: scater::plotReducedDim;\n"
        "  SingleCellExperiment: scater (plotUMAP, plotExpression, plotColData, plotHeatmap);\n"
        "  SpatialExperiment: ggspavis (plotSpots, plotVisium, plotSpotQC).\n"
        "  Use ggplot2 only to adjust a plot these return, or when none of them fits,\n"
        "  and say so when you do.\n"
        "- To fix or redo a cell, pass its cell_id instead of creating a new cell.\n\n"
        "Rules:\n"
        "- Never simulate, mock or invent data or results. If the data or a package is\n"
        "  not available, say so and suggest an installed alternative.\n"
        "- Do not install packages.\n"
        "- After an error: read it, look up the function with r_help, fix and re-run\n"
        "  once. If it fails again, stop and explain.\n"
        "- Do the step the user asked for, show the result, then stop and suggest the\n"
        "  next step rather than doing it."
    ),
)

@mcp.tool(
    name="execute_r_cell",
    description=(
        "Execute an R code cell in the note's persistent R session. Variables, objects, and loaded libraries stay in memory. "
        "Captures output, plots, and tables. Keep steps small (a few lines per cell). "
        "Only reference objects already created in earlier successful cells (or verify with ls()). "
        "Plot with the ecosystem's functions (miaViz, scater, ggspavis) before reaching for ggplot2. "
        "Pass 'cell_id' when editing or re-running a cell to update it in place instead of appending a new cell. "
        "Do not install packages; suggest an installed alternative if a package is unavailable."
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
                f"status={status} -->\n[cell_id: \"{saved['cellId']}\"]\n")
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
    description=(
        "Search the pinned Bioconductor QMD knowledge index across 12 curated books: "
        "1. osca (Single-Cell Analysis), 2. osca-basic (Single-Cell Basics), 3. osca-advanced (Single-Cell Advanced), "
        "4. scrapbook (Single-Cell with scrapper), 5. osta (Spatial Transcriptomics), 6. tidy-spatial (Tidy Spatial Analysis), "
        "7. oma (Microbiome Analysis), 8. rnaseq-gene (RNA-seq Gene-Level & DE), 9. tidyomics (Tidyomics Tutorials), "
        "10. mofa2 (Multi-Omics Factor Analysis), 11. r-for-mass-spectrometry (Mass Spectrometry), 12. metabonaut (Metabolomics). "
        "Primary authority for canonical workflows, domain packages, and visualization idioms. "
        "Consult this tool before generating analysis code to ensure standard Bioconductor methodology."
    )
)
def search_bioc_books(query: str, book: str = "", limit: int = 4) -> str:
    """
    Search curated Bioconductor books for canonical R code snippets and workflow guidance.
    Optional books: 'osca', 'osca-basic', 'osca-advanced', 'scrapbook', 'osta', 'tidy-spatial',
    'oma', 'rnaseq-gene', 'tidyomics', 'mofa2', 'r-for-mass-spectrometry', 'metabonaut'.
    Leave empty to search across all 12 books.
    """
    book_filter = book.strip().lower() if book and book.strip() else None
    res = search_bioc_knowledge(query=query, book=book_filter, limit=limit)
    return res.get("markdown", "")


R_LIBRARY_DIRS = ("/usr/local/lib/R/site-library", "/usr/local/lib/R/library")
_help_index_cache = None
_help_index_lock = threading.Lock()


def _help_index() -> dict:
    """Map help aliases to the installed packages that document them (read once from help/AnIndex)."""
    global _help_index_cache
    with _help_index_lock:
        if _help_index_cache is None:
            index = {}
            for lib in R_LIBRARY_DIRS:
                if not os.path.isdir(lib):
                    continue
                for pkg in os.listdir(lib):
                    try:
                        with open(os.path.join(lib, pkg, "help", "AnIndex"), encoding="utf-8", errors="replace") as f:
                            for line in f:
                                alias = line.split("\t", 1)[0]
                                if alias:
                                    index.setdefault(alias, set()).add(pkg)
                    except OSError:
                        continue
            _help_index_cache = index
        return _help_index_cache


# Build the alias index in the background at startup; reading ~750 indexes from a cold disk takes seconds.
threading.Thread(target=_help_index, daemon=True).start()


def _run_r_helper_script(r_code: str, timeout: int = 15) -> str:
    """Run a fast, isolated R snippet via Rscript and return stdout."""
    try:
        res = subprocess.run(
            ["Rscript", "--vanilla", "-e", r_code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = res.stdout.strip()
        if not out and res.stderr:
            err = res.stderr.strip()
            if res.returncode != 0:
                return f"Error querying R: {err}"
        return out or "No output returned."
    except subprocess.TimeoutExpired:
        return "R helper query timed out."
    except Exception as e:
        return f"Error executing R helper: {str(e)}"


@mcp.tool(
    name="r_help",
    description=(
        "Look up canonical documentation (Usage, Arguments, Value, and Examples) for an installed R function or topic. "
        "Call this before writing code for any unfamiliar package function to verify exact argument names and signatures. "
        "Runs in a fast, isolated process without adding a visible cell to the note canvas."
    )
)
def r_help(topic: str, package: str = "") -> str:
    """
    Look up documentation for an R function/topic from installed packages.
    Args:
        topic: Function or topic name (e.g. 'agglomerateByRank', 'DESeq', 'runPCA').
        package: Optional package name to narrow search (e.g. 'mia', 'DESeq2', 'scater').
    """
    topic = topic.strip()
    package = package.strip()
    if not re.match(r"^[A-Za-z0-9._]+$", topic):
        return f"Invalid topic name: '{topic}'. Must contain only letters, numbers, dots, and underscores."
    if package and not re.match(r"^[A-Za-z0-9._]+$", package):
        return f"Invalid package name: '{package}'. Must contain only letters, numbers, dots, and underscores."

    if not package:
        owners = sorted(_help_index().get(topic, ()))
        if not owners:
            return f"Help topic '{topic}' was not found in any installed package."
        if len(owners) > 1:
            return (f"Help topic '{topic}' is documented in several installed packages: "
                    f"{', '.join(owners)}. Call r_help again with the package you mean.")
        package = owners[0]

    r_script = f"""
    topic <- "{topic}"
    pkg <- "{package}"

    if (length(find.package(pkg, quiet = TRUE)) == 0) {{
        cat(sprintf("Package '%s' is not installed in the R environment.", pkg))
        quit(save = "no", status = 0)
    }}

    h <- help(topic, package = (pkg))
    if (length(h) == 0) {{
        cat(sprintf("Help topic '%s' was not found in package '%s'.", topic, pkg))
        quit(save = "no", status = 0)
    }}
    cat(sprintf("Package: %s\\n\\n", pkg))

    rd <- utils:::.getHelpFile(h)
    tmp <- tempfile()
    on.exit(unlink(tmp))
    tools::Rd2txt(rd, out = tmp, stages = c("build", "render"), options = list(underline_titles = FALSE))
    txt <- readChar(tmp, file.info(tmp)$size)
    if (nchar(txt) > 4000) {{
        txt <- paste0(substr(txt, 1, 3950), "\\n... [documentation truncated to 4000 characters]")
    }}
    cat(txt)
    """
    return _run_r_helper_script(r_script)


@mcp.tool(
    name="list_datasets",
    description=(
        "List all real bundled example datasets available inside an installed R package. "
        "Use this to find real data (e.g. GlobalPatterns, airway, ZeiselBrainData) instead of inventing or mocking data. "
        "Runs in a fast, isolated process without adding a visible cell to the note canvas."
    )
)
def list_datasets(package: str) -> str:
    """
    List datasets available in an installed package.
    Args:
        package: Name of the package (e.g. 'mia', 'scRNAseq', 'airway', 'pasilla').
    """
    package = package.strip()
    if not re.match(r"^[A-Za-z0-9._]+$", package):
        return f"Invalid package name: '{package}'. Must contain only letters, numbers, dots, and underscores."

    r_script = f"""
    pkg <- "{package}"
    if (length(find.package(pkg, quiet = TRUE)) == 0) {{
        cat(sprintf("Package '%s' is not installed in the R environment.", pkg))
        quit(save = "no", status = 0)
    }}
    
    d <- data(package = pkg)$results
    lines <- if (is.null(d) || nrow(d) == 0) character() else sprintf("- %s: %s", d[, "Item"], d[, "Title"])

    # Experiment-data packages (STexampleData, scRNAseq, imcdatasets, ...) expose datasets as loader functions.
    views <- packageDescription(pkg)$biocViews
    if (!is.null(views) && grepl("ExperimentData|ExperimentHub", views)) {{
        tag <- function(rd, t) unlist(lapply(rd[vapply(rd, function(x) identical(attr(x, "Rd_tag"), t), logical(1))], as.character))
        title <- character()
        for (rd in tools::Rd_db(pkg)) {{
            t <- trimws(gsub("\\\\s+", " ", paste(tag(rd, "\\\\title"), collapse = "")))
            for (a in tag(rd, "\\\\alias")) title[a] <- t
        }}
        # Read exports from NAMESPACE rather than loading the package (hub packages load slowly).
        ns <- parseNamespaceFile(pkg, dirname(find.package(pkg)))
        ex <- ns$exports
        if (length(ns$exportPatterns)) ex <- c(ex, grep(paste(ns$exportPatterns, collapse = "|"), names(title), value = TRUE))
        # Hub packages that create accessors at load time (createHubAccessors) declare no exports; use their documented aliases.
        if (!length(ex)) ex <- setdiff(grep("-package$|-class$|-method|^[.]", names(title), value = TRUE, invert = TRUE), pkg)
        ex <- sort(unique(ex[ex %in% names(title)]))
        if (length(ex)) lines <- c(lines, sprintf("- %s(): %s", ex, title[ex]))
    }}

    if (length(lines) == 0) {{
        cat(sprintf("Package '%s' has no bundled example datasets.", pkg))
        quit(save = "no", status = 0)
    }}
    out <- paste(lines, collapse = "\\n")
    if (nchar(out) > 4000) {{
        out <- paste0(substr(out, 1, 3950), "\\n... [dataset list truncated]")
    }}
    cat(out)
    """
    return _run_r_helper_script(r_script)


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

def _resolve_project_dir(project_id: str) -> str:
    """Resolve project directory under /app/projects/{project_id} or /app/projects/users/*/{project_id}."""
    direct = os.path.join(PROJECTS_DIR, project_id)
    if os.path.isdir(direct):
        return direct
    users_root = os.path.join(PROJECTS_DIR, "users")
    if os.path.isdir(users_root):
        for user_dir in os.listdir(users_root):
            candidate = os.path.join(users_root, user_dir, project_id)
            if os.path.isdir(candidate):
                try:
                    if not os.path.exists(direct) and not os.path.islink(direct):
                        rel_target = os.path.join("users", user_dir, project_id)
                        os.symlink(rel_target, direct)
                except Exception:
                    pass
                return candidate
    return direct

@mcp.tool(
    name="render_quarto_report",
    description="Compile the Quarto website project into HTML for the given project_id. Returns compilation status, stdout, stderr, and the live preview URL."
)
def render_quarto_report(project_id: str = "default") -> str:
    """
    Compile the Quarto project at /app/projects/{project_id} into an HTML website.
    """
    import subprocess
    project_dir = _resolve_project_dir(project_id)
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
    project_dir = _resolve_project_dir(project_id)
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
    project_dir = _resolve_project_dir(project_id)
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
