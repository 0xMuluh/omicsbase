import logging
import os
import json
import time
import uuid
import pandas as pd
from typing import Callable, Dict, Any, List, Optional

from engine.kernel.note_kernel import (
    ensure_kernel,
    request_cell,
    KernelBusy,
    KernelCancelled,
    KernelTimeout,
    CONSOLE_FILE_NAME,
    EVENTS_FILE_NAME,
)

import re

logger = logging.getLogger(__name__)

# Console text returned per cell, to the model and to the note: the start and the end.
OUTPUT_MAX_CHARS = int(os.environ.get("NOTE_OUTPUT_MAX_CHARS", "20000"))
OUTPUT_TAIL_CHARS = OUTPUT_MAX_CHARS // 5
# The shortest run a cell still gets after waiting for a free R session.
MIN_RUN_SECONDS = 30


def _size_label(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} bytes"


def read_console(path: str, max_chars: int = OUTPUT_MAX_CHARS) -> str:
    """The cell's console text, or its start and end with a note of what was left out."""
    if not os.path.exists(path):
        return ""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= max_chars:
            return f.read().decode("utf-8", errors="replace")
        tail_chars = min(OUTPUT_TAIL_CHARS, max_chars)
        head = f.read(max_chars - tail_chars).decode("utf-8", errors="ignore")
        f.seek(size - tail_chars)
        tail = f.read().decode("utf-8", errors="ignore")
    omitted = size - (max_chars - tail_chars) - tail_chars
    return (
        f"{head}\n\n[... {_size_label(omitted)} of output omitted. Print a summary, "
        f"head() or dim() instead of the whole object.]\n\n{tail}"
    )

BLOCKED_INSTALL_PATTERNS = re.compile(
    r"(?:(?:utils::)?install\.packages\s*\(|"
    r"BiocManager::install\s*\(|"
    r"remotes::install_\w+\s*\(|"
    r"devtools::install_\w+\s*\(|"
    r"pak::(?:pkg_install|pak)\s*\(|"
    r"pacman::p_(?:load|install)\s*\()",
    re.IGNORECASE,
)


def check_blocked_package_install(code: str) -> Optional[str]:
    """Return matching blocked install pattern if found in active code (ignoring comments)."""
    for line in code.splitlines():
        clean_line = line.strip()
        if clean_line.startswith("#"):
            continue
        code_part = clean_line.split("#", 1)[0]
        match = BLOCKED_INSTALL_PATTERNS.search(code_part)
        if match:
            return match.group(0).strip(" (")
    return None


def execute_note_cell(
    projects_dir: str,
    thread_id: str,
    code: str,
    base_url: str = "http://localhost:8001",
    timeout_seconds: int = 180,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Executes an R cell in the thread's persistent R kernel.
    Returns console output, formatted markdown, and plot/table URLs for LibreChat.
    """
    thread_dir = os.path.join(projects_dir, thread_id)
    os.makedirs(thread_dir, exist_ok=True)

    cell_id = uuid.uuid4().hex[:8]
    run_dir_rel = os.path.join("runs", f"cell_{cell_id}")
    run_dir_full = os.path.join(thread_dir, run_dir_rel)
    os.makedirs(run_dir_full, exist_ok=True)

    blocked_call = check_blocked_package_install(code)
    if blocked_call:
        msg = (
            f"Dynamic package installation via '{blocked_call}' is disabled in OmicsBase. "
            "All required analysis packages (754 pre-compiled Bioconductor and CRAN packages) are already built into the container environment. "
            "If a package is truly missing, report it to the user or administrator instead of attempting runtime installation."
        )
        return {
            "success": False,
            "error": msg,
            "stdout": f"[Package Installation Blocked]: {msg}",
            "markdown": (
                f"❌ **Package Installation Blocked**\n\n"
                f"Runtime package installation (`{blocked_call}`) is disabled in OmicsBase.\n\n"
                f"{msg}"
            ),
            "cell_id": cell_id,
            "engine_run_dir": run_dir_rel,
            "run_dir": run_dir_rel,
        }

    try:
        started = time.monotonic()
        handle = ensure_kernel(thread_dir)
        # Time spent waiting for a free session comes out of the cell's own limit,
        # so the caller's timeout still holds.
        waited = time.monotonic() - started
        done_payload = request_cell(
            handle,
            request_id=cell_id,
            run_dir_rel=run_dir_rel,
            source=code,
            timeout_seconds=max(MIN_RUN_SECONDS, int(timeout_seconds - waited)),
            cancel_check=cancel_check,
        )
    except KernelBusy as e:
        return {
            "success": False,
            "error": str(e),
            "stdout": f"[Engine Busy]: {str(e)}",
            "markdown": f"**Engine busy:** {str(e)} R was not run.",
            "cell_id": cell_id,
            "engine_run_dir": run_dir_rel,
            "run_dir": run_dir_rel,
        }
    except KernelCancelled as e:
        return {
            "success": False,
            "cancelled": True,
            "error": str(e),
            "stdout": f"[Kernel Cancelled]: {str(e)}",
            "markdown": f"**Cancelled:**\n```\n{str(e)}\n```",
            "cell_id": cell_id,
            "engine_run_dir": run_dir_rel,
            "run_dir": run_dir_rel,
        }
    except KernelTimeout as e:
        return {
            "success": False,
            "timed_out": True,
            "error": str(e),
            "stdout": f"[Kernel Timeout]: {str(e)}",
            "markdown": f"**Timed out:**\n```\n{str(e)}\n```",
            "cell_id": cell_id,
            "engine_run_dir": run_dir_rel,
            "run_dir": run_dir_rel,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "stdout": f"[Kernel Error]: {str(e)}",
            "markdown": f"**Kernel Execution Error:**\n```\n{str(e)}\n```",
            "cell_id": cell_id,
            "engine_run_dir": run_dir_rel,
            "run_dir": run_dir_rel,
        }

    stdout = read_console(os.path.join(run_dir_full, CONSOLE_FILE_NAME))

    events_path = os.path.join(run_dir_full, EVENTS_FILE_NAME)
    events: List[Dict[str, Any]] = []
    if os.path.exists(events_path):
        with open(events_path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass

    plots_dir = os.path.join(run_dir_full, "plots")
    plot_urls = []
    if os.path.exists(plots_dir):
        for p in sorted(os.listdir(plots_dir)):
            if p.endswith(".png") and os.path.getsize(os.path.join(plots_dir, p)) > 100:
                url = f"{base_url}/projects/{thread_id}/{run_dir_rel}/plots/{p}"
                plot_urls.append(url)

    # Table sizes as R reported them, so a preview never reads a whole table.
    table_sizes = {
        os.path.basename(str(event.get("path", ""))): (event.get("rows"), event.get("cols"))
        for event in events
        if event.get("type") == "table"
    }
    tables_dir = os.path.join(run_dir_full, "tables")
    table_previews = []
    if os.path.exists(tables_dir):
        for t in sorted(os.listdir(tables_dir)):
            if t.endswith(".csv"):
                t_path = os.path.join(tables_dir, t)
                try:
                    df = pd.read_csv(t_path, nrows=10)
                    n_rows, n_cols = table_sizes.get(t, (None, None))
                    if n_rows is None:
                        with open(t_path, "rb") as f:
                            n_rows = max(0, sum(1 for _ in f) - 1)
                    cols = [str(c) for c in df.columns]
                    header = "| " + " | ".join(cols) + " |"
                    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
                    rows = [
                        "| " + " | ".join(str(val) for val in r) + " |"
                        for _, r in df.iterrows()
                    ]
                    table_md = "\n".join([header, sep] + rows)
                    table_previews.append(
                        {
                            "file": t,
                            "rows": int(n_rows),
                            "cols": int(n_cols if n_cols is not None else len(df.columns)),
                            "markdown": table_md,
                            "url": f"{base_url}/projects/{thread_id}/{run_dir_rel}/tables/{t}",
                        }
                    )
                except Exception as ex:
                    logger.warning("Failed to render table %s: %s", t, ex)

    md_parts = []
    if stdout.strip():
        md_parts.append(f"```r\n{stdout.strip()}\n```")

    for tbl in table_previews:
        md_parts.append(
            f"**Table ({tbl['rows']} rows x {tbl['cols']} cols):**\n\n{tbl['markdown']}\n"
        )

    for p_url in plot_urls:
        md_parts.append(f"![Plot Output]({p_url})")

    full_markdown = "\n\n".join(md_parts) if md_parts else "(Cell executed with no output)"

    errors = [
        str(event.get("content", "R execution failed"))[:2000]
        for event in events
        if event.get("type") == "error"
    ][:20]
    return {
        "success": not errors,
        "error": "\n".join(errors) if errors else None,
        "cell_id": cell_id,
        "stdout": stdout,
        "plots": plot_urls,
        "tables": table_previews,
        "markdown": full_markdown,
        "engine_run_dir": run_dir_rel,
        "run_dir": run_dir_rel,
        "done": done_payload,
    }
