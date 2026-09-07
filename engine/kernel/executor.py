import logging
import os
import json
import uuid
import pandas as pd
from typing import Callable, Dict, Any, List, Optional

from engine.kernel.note_kernel import (
    ensure_kernel,
    request_cell,
    KernelCancelled,
    KernelTimeout,
    CONSOLE_FILE_NAME,
    EVENTS_FILE_NAME,
)

logger = logging.getLogger(__name__)


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

    try:
        handle = ensure_kernel(thread_dir)
        done_payload = request_cell(
            handle,
            request_id=cell_id,
            run_dir_rel=run_dir_rel,
            source=code,
            timeout_seconds=timeout_seconds,
            cancel_check=cancel_check,
        )
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

    console_path = os.path.join(run_dir_full, CONSOLE_FILE_NAME)
    stdout = ""
    if os.path.exists(console_path):
        with open(console_path, "r", errors="ignore") as f:
            stdout = f.read()

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

    tables_dir = os.path.join(run_dir_full, "tables")
    table_previews = []
    if os.path.exists(tables_dir):
        for t in sorted(os.listdir(tables_dir)):
            if t.endswith(".csv"):
                t_path = os.path.join(tables_dir, t)
                try:
                    df = pd.read_csv(t_path)
                    cols = [str(c) for c in df.columns]
                    header = "| " + " | ".join(cols) + " |"
                    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
                    rows = [
                        "| " + " | ".join(str(val) for val in r) + " |"
                        for _, r in df.head(10).iterrows()
                    ]
                    table_md = "\n".join([header, sep] + rows)
                    table_previews.append(
                        {
                            "file": t,
                            "rows": len(df),
                            "cols": len(df.columns),
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

    errors = [str(event.get("content", "R execution failed")) for event in events if event.get("type") == "error"]
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
