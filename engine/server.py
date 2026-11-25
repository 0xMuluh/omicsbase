import os
import asyncio
import contextlib
import fcntl
import hmac
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from starlette.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.requests import Request
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from engine.kernel import cell_status, session_memory
from engine.kernel.executor import execute_note_cell
from engine.kernel.sandbox import confined_command
from engine.kernel.note_kernel import (
    DONE_PREFIX, EXECUTE_LOCK_NAME, PID_FILE_NAME, REQUEST_FILE_NAME, R_START_SECONDS, START_LOCK_NAME,
    save_all_kernels, start_reaper,
)
from engine.knowledge.book_refs import log_lookups
from engine.knowledge.search import search_bioc_knowledge
from engine import note_pods, note_workers

PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/app/projects")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")
# Shared with LibreChat only; falls back to the Reports secret when not set separately.
INTERNAL_SECRET = os.environ.get("OMICSBASE_ENGINE_SECRET") or os.environ.get("OMICSBASE_AUTH_SECRET", "")
CELL_TIMEOUT_SECONDS = 600

logger = logging.getLogger("engine")

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
        "- Microbial abundance data (TreeSummarizedExperiment): use mia for each step,\n"
        "  e.g. agglomerateByRank, agglomerateByPrevalence or subsetByPrevalent,\n"
        "  transformAssay, addAlpha, getDissimilarity, getPERMANOVA, addRDA; miaTime\n"
        "  for time series.\n"
        "- Comparing abundances between groups, when the user names no method: follow\n"
        "  the differential abundance chapter of the OMA book (search it first): MaAsLin2\n"
        "  for a two-group comparison and for covariates or interactions, maaslin3 for\n"
        "  differences in prevalence and in non-zero abundances, miaViz::plotBoxplot to\n"
        "  show the results. Report BH-adjusted q-values. A method the user names always\n"
        "  comes first.\n"
        "- Plot with the ecosystem's plotting functions:\n"
        "  TreeSummarizedExperiment: miaViz (plotAbundance, plotBoxplot, plotPrevalence,\n"
        "  plotRowTree, plotSeries, plotDMNFit, plotRDA, plotLoadings);\n"
        "  reduced dimensions and ordinations: scater::plotReducedDim;\n"
        "  SingleCellExperiment: scater (plotUMAP, plotExpression, plotColData, plotHeatmap);\n"
        "  SpatialExperiment: ggspavis (plotSpots, plotVisium, plotSpotQC).\n"
        "  Use ggplot2 only to adjust a plot these return, or when none of them fits,\n"
        "  and say so when you do.\n"
        "- To fix or redo a cell, pass its cell_id instead of creating a new cell.\n"
        "- Files a cell saves in the note's working directory appear as download links\n"
        "  under the cell, so give the user the file name rather than a path or link.\n\n"
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
    """Run R without blocking the ASGI loop. OmicsBase answers this tool itself (it saves the
    cell and runs it through /api/execute, so the engine never calls back); this serves other
    MCP clients, which keep no cells, so cell_id is not used here."""
    return await asyncio.to_thread(_execute_agent_cell, code, thread_id)


def _execute_agent_cell(code, thread_id):
    try:
        result = _run_cell(code, thread_id, CELL_TIMEOUT_SECONDS)
    except Exception as exc:
        return f"Execution failed: {exc}"
    return result.get("markdown", "")


def _cancel_path(thread_id, execution_id=None):
    if execution_id and (len(execution_id) != 24 or any(c not in "0123456789abcdef" for c in execution_id)):
        raise ValueError("Invalid execution ID")
    name = f"cancel-{execution_id}.flag" if execution_id else "cancel.flag"
    return os.path.join(PROJECTS_DIR, thread_id, ".omicsbase", "note-kernel", name)


def _note_dir(thread_id):
    return os.path.join(PROJECTS_DIR, thread_id)


def _run_cell(code, thread_id, timeout_seconds, execution_id=None, wait=True):
    """Run one cell and return its result. A cell with an execution id also leaves a status
    record, read by /api/executions. `wait` false: the caller follows that record, so the
    cell may wait for the note's pod as long as it takes."""
    cancel_path = _cancel_path(thread_id, execution_id)
    report = cell_status.reporter(_note_dir(thread_id), execution_id)
    if note_pods.ENABLED:
        # The note's own pod runs the cell, takes the note's lock and sees cancel flags.
        payload = {"code": code, "thread_id": thread_id, "execution_id": execution_id,
                   "timeout_seconds": timeout_seconds}
        report("starting_pod")
        result = note_pods.run_cell(
            thread_id, payload, timeout_seconds + R_START_SECONDS + 60 if wait else None,
            on_phase=report, cancelled=lambda: bool(execution_id) and os.path.exists(cancel_path),
        )
        if result.get("cancelled"):
            Path(cancel_path).unlink(missing_ok=True)
        report("done", done=True, result=result)
        return result
    if not execution_id:
        Path(cancel_path).unlink(missing_ok=True)
    lock_path = Path(cancel_path).parent / "execute.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    report("waiting_for_cell")
    try:
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cell_status.remove_old(_note_dir(thread_id))
            if os.path.exists(cancel_path):
                result = {"success": False, "cancelled": True, "error": "Cancelled",
                          "markdown": "Execution cancelled before starting."}
            else:
                result = execute_note_cell(
                    projects_dir=PROJECTS_DIR, thread_id=thread_id, code=code, base_url=BASE_URL,
                    timeout_seconds=timeout_seconds, cancel_check=lambda: os.path.exists(cancel_path),
                    on_status=report,
                )
    finally:
        Path(cancel_path).unlink(missing_ok=True)
    report("done", done=True, result=result)
    return result


def _run_cell_in_background(code, thread_id, timeout_seconds, execution_id):
    try:
        _run_cell(code, thread_id, timeout_seconds, execution_id, wait=False)
    except Exception as exc:
        logger.exception("cell %s of note %s failed", execution_id, thread_id)
        message = f"Execution failed: {exc}"
        cell_status.update(_note_dir(thread_id), execution_id, "done", done=True,
                           result={"success": False, "error": message, "markdown": f"**{message}**"})


def _interrupted(thread_id, record):
    """Whether an unfinished run has no one working on it any more."""
    if note_pods.ENABLED and record.get("phase") in cell_status.POD_PHASES:
        return not note_pods.serving(thread_id)
    return record.get("boot_id") != cell_status.BOOT_ID


def _execution_status(thread_id, execution_id, offset):
    note_dir = _note_dir(thread_id)
    record = cell_status.read(note_dir, execution_id)
    if record is None:
        return None
    if not record.get("done") and _interrupted(thread_id, record):
        record = cell_status.update(note_dir, execution_id, "interrupted", done=True,
                                    result=cell_status.INTERRUPTED)
    output, next_offset = cell_status.read_output(note_dir, record, offset)
    return {
        "phase": record.get("phase"),
        "phase_started_at": record.get("phase_started_at"),
        "started_at": record.get("started_at"),
        "cancel_requested": os.path.exists(_cancel_path(thread_id, execution_id)),
        "output": output,
        "next_offset": next_offset,
        "done": bool(record.get("done")),
        "result": record.get("result"),
    }


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
def search_bioc_books(query: str, book: str = "", limit: int = 4, thread_id: str = "") -> str:
    """
    Search curated Bioconductor books for canonical R code snippets and workflow guidance.
    Optional books: 'osca', 'osca-basic', 'osca-advanced', 'scrapbook', 'osta', 'tidy-spatial',
    'oma', 'rnaseq-gene', 'tidyomics', 'mofa2', 'r-for-mass-spectrometry', 'metabonaut'.
    Leave empty to search across all 12 books. 'thread_id' is the note the search is made for.
    """
    book_filter = book.strip().lower() if book and book.strip() else None
    res = search_bioc_knowledge(query=query, book=book_filter, limit=limit)
    _log_knowledge_lookups(thread_id, res)
    return res.get("markdown", "")


def _log_knowledge_lookups(thread_id, result):
    """Remember which book sections were looked up in a note; the book badge prefers them."""
    if not thread_id or not _NOTE_ID.match(str(thread_id)):
        return
    try:
        log_lookups(os.path.join(PROJECTS_DIR, str(thread_id)), result.get("matches", []))
    except Exception:
        pass


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
        # The code is built from tool arguments: run it confined to a throwaway folder.
        with tempfile.TemporaryDirectory(prefix="r-help-") as folder:
            argv, env = confined_command(folder, ["Rscript", "--vanilla", "-e", r_code])
            res = subprocess.run(
                argv,
                env=env,
                cwd=folder,
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
        argv, env = confined_command(project_dir, ["quarto", "render", project_dir])
        proc = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=300,
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

# The R sessions are child processes and get no signal of their own when the engine is
# stopped (e.g. Rahti moving the pod), so save them during the app's shutdown. Exit
# hooks would be too late: uvicorn re-raises SIGTERM after shutting down.
_serve_lifespan = app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan_saving_sessions(application):
    async with _serve_lifespan(application) as state:
        yield state
    await asyncio.to_thread(save_all_kernels)


app.router.lifespan_context = _lifespan_saving_sessions

from engine.access import ProjectAccess
app.add_middleware(ProjectAccess)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("DOMAIN_CLIENT", "http://localhost:3080")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def handle_health(request: Request):
    """Says the engine is up. No secret and no R, so an install can check its ENGINE_URL."""
    return JSONResponse({"status": "ok"})


app.add_route("/health", handle_health, methods=["GET"])

# Server-side execute only (LibreChat control plane). Browser must not call this.
async def handle_execute(request: Request):
    if not _internal_caller(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)

    code = data.get("code", "")
    thread_id = data.get("thread_id", "default")
    timeout_seconds = int(data.get("timeout_seconds") or CELL_TIMEOUT_SECONDS)
    if not code.strip():
        return JSONResponse({"success": False, "error": "Code cannot be empty"}, status_code=400)

    execution_id = data.get("execution_id")
    if data.get("wait") is False:
        # The cell runs on; the caller follows it through /api/executions.
        try:
            if not execution_id:
                raise ValueError("wait: false needs an execution_id")
            _cancel_path(thread_id, execution_id)
        except ValueError as err:
            return JSONResponse({"success": False, "error": str(err)}, status_code=400)
        phase = "starting_pod" if note_pods.ENABLED else "waiting_for_cell"
        cell_status.start(_note_dir(thread_id), execution_id, phase)
        threading.Thread(
            target=_run_cell_in_background, args=(code, thread_id, timeout_seconds, execution_id),
            name=f"cell-{execution_id}", daemon=True,
        ).start()
        return JSONResponse({"execution_id": execution_id, "phase": phase}, status_code=202)

    result = await asyncio.to_thread(_run_cell, code, thread_id, timeout_seconds, execution_id)
    if result.get("cancelled"):
        result["success"] = False
    return JSONResponse(result)


async def handle_execution_status(request: Request):
    """A run's phase, its console text from `offset`, and its result once done."""
    if not _internal_caller(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    thread_id = request.query_params.get("thread_id", "default")
    execution_id = request.path_params["execution_id"]
    try:
        offset = max(0, int(request.query_params.get("offset") or 0))
        _cancel_path(thread_id, execution_id)
    except ValueError:
        return JSONResponse({"success": False, "error": "Invalid execution ID or offset"}, status_code=400)
    status = await asyncio.to_thread(_execution_status, thread_id, execution_id, offset)
    if status is None:
        return JSONResponse({"success": False, "error": "Unknown execution"}, status_code=404)
    return JSONResponse(status)


async def handle_cancel(request: Request):
    if not _internal_caller(request):
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


async def handle_sessions(request: Request):
    """The notes whose R sessions this engine hosts and their memory, for a gateway's placement."""
    if not _internal_caller(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    return JSONResponse({"sessions": await asyncio.to_thread(session_memory.report, PROJECTS_DIR)})


app.add_route("/api/execute", handle_execute, methods=["POST"])
app.add_route("/api/cancel", handle_cancel, methods=["POST"])
app.add_route("/api/executions/{execution_id}", handle_execution_status, methods=["GET"])
app.add_route("/api/sessions", handle_sessions, methods=["GET"])


# Note files arrive and are copied through the engine, so LibreChat needs no access to
# the engine's disk (the engine may run on another platform).
_NOTE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# A live session's process and lock files; a copied note starts a session of its own.
_SESSION_RUNTIME_FILES = shutil.ignore_patterns(
    PID_FILE_NAME, START_LOCK_NAME, EXECUTE_LOCK_NAME, REQUEST_FILE_NAME, f"{DONE_PREFIX}*", "*.flag",
    f"{cell_status.STATUS_PREFIX}*",
)


def _internal_caller(request: Request) -> bool:
    provided = request.headers.get("x-internal-secret", "")
    return bool(INTERNAL_SECRET and provided and hmac.compare_digest(provided, INTERNAL_SECRET))


async def handle_put_data_file(request: Request):
    """Store an uploaded file as data/<filename> in a note. An existing copy is kept.

    HEAD answers 200 or 404, so callers can skip uploading a file the note already has.
    """
    if not _internal_caller(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    thread_id = request.path_params["project_id"]
    filename = request.path_params["filename"]
    if not _NOTE_ID.match(thread_id) or os.path.basename(filename) != filename or filename.startswith("."):
        return JSONResponse({"success": False, "error": "Invalid note or file name"}, status_code=400)
    data_dir = Path(PROJECTS_DIR) / thread_id / "data"
    target = data_dir / filename
    if request.method == "HEAD":
        return Response(status_code=200 if target.is_file() and not target.is_symlink() else 404)
    data_dir.mkdir(parents=True, exist_ok=True)
    partial = data_dir / f".upload-{uuid.uuid4().hex}"
    try:
        with open(partial, "wb") as handle:
            async for chunk in request.stream():
                handle.write(chunk)
        try:
            os.link(partial, target)
        except FileExistsError:
            pass
    finally:
        partial.unlink(missing_ok=True)
    if target.is_symlink() or not target.is_file():
        return JSONResponse({"success": False, "error": "Invalid attachment target"}, status_code=409)
    return JSONResponse({"success": True, "workspacePath": f"data/{filename}", "bytes": target.stat().st_size})


def _without_data(source: Path):
    """Also leave out the uploaded files (data/) and the saved R session, which holds the
    objects made from them; code, results and figures are still copied."""
    def ignore(directory, names):
        skipped = set(_SESSION_RUNTIME_FILES(directory, names))
        here = Path(directory)
        if here == source:
            skipped |= {"data"} & set(names)
        elif here == source / ".omicsbase":
            skipped |= {"note-kernel"} & set(names)
        return skipped
    return ignore


async def handle_copy_note(request: Request):
    """Copy another note's files into this one, as when a conversation is forked.
    With {"include_data": false}, the copy has no uploaded files and no saved R session."""
    if not _internal_caller(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
        source_id = body.get("source", "")
        include_data = body.get("include_data", True) is not False
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)
    target_id = request.path_params["project_id"]
    if not _NOTE_ID.match(target_id) or not _NOTE_ID.match(str(source_id)):
        return JSONResponse({"success": False, "error": "Invalid note id"}, status_code=400)
    source = Path(PROJECTS_DIR) / source_id
    if not source.is_dir():
        return JSONResponse({"success": True, "copied": False})
    await asyncio.to_thread(
        shutil.copytree, source, Path(PROJECTS_DIR) / target_id,
        ignore=_SESSION_RUNTIME_FILES if include_data else _without_data(source),
        symlinks=True, dirs_exist_ok=True,
    )
    return JSONResponse({"success": True, "copied": True})


app.add_route("/api/projects/{project_id}/data/{filename}", handle_put_data_file, methods=["PUT", "HEAD"])
app.add_route("/api/projects/{project_id}/copy", handle_copy_note, methods=["POST"])

# Direct HTTP endpoint for frontend knowledge queries
async def handle_knowledge_search(request: Request):
    q = request.query_params.get("q", "")
    book = request.query_params.get("book", None)
    limit = int(request.query_params.get("limit", 4))
    res = search_bioc_knowledge(query=q, book=book, limit=limit)
    # Only OmicsBase itself may write to a note's look-up log.
    if _internal_caller(request):
        _log_knowledge_lookups(request.query_params.get("thread_id", ""), res)
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
        
    argv, env = confined_command(project_dir, ["quarto", "render", project_dir])
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=project_dir,
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
    if note_pods.ENABLED:
        gateway = note_workers if note_workers.ENABLED else note_pods
        gateway.start(INTERNAL_SECRET, lambda thread_id: cell_status.active_runs(_note_dir(thread_id)))
    else:
        start_reaper()
        if session_memory.LIMIT:
            session_memory.start_watchdog()
    uvicorn.run(app, host="0.0.0.0", port=8001)
