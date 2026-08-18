"""Bounded R inspection for the workspace agent (package data / structure probes)."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.services.runner import run_command_sync

logger = logging.getLogger(__name__)

MAX_CODE_CHARS = 4000
MAX_OUTPUT_CHARS = 12000
DEFAULT_TIMEOUT_SECONDS = 30

_BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdownload\.file\b", re.I), "download.file is not allowed"),
    (re.compile(r"\burl\s*\(", re.I), "url() is not allowed"),
    (re.compile(r"\bcurl\b", re.I), "curl is not allowed"),
    (re.compile(r"\bhttr\b", re.I), "httr is not allowed"),
    (re.compile(r"\bhttr2\b", re.I), "httr2 is not allowed"),
    (re.compile(r"\brvest\b", re.I), "rvest is not allowed"),
    (re.compile(r"\bsystem2?\s*\(", re.I), "system/system2 is not allowed"),
    (re.compile(r"\binstall\.packages\b", re.I), "install.packages is not allowed"),
    (re.compile(r"\bbiocmanager::install\b", re.I), "package installation is not allowed"),
    (re.compile(r"\bdevtools::", re.I), "devtools is not allowed"),
    (re.compile(r"\bremotes::", re.I), "remotes is not allowed"),
    (re.compile(r"\bpak::", re.I), "pak is not allowed"),
    (re.compile(r"\bwrite[_.]", re.I), "write_* is not allowed"),
    (re.compile(r"\bggsave\b", re.I), "ggsave is not allowed"),
    (re.compile(r"\bsaveRDS\b", re.I), "saveRDS is not allowed"),
    (re.compile(r"\bsave\s*\(", re.I), "save() is not allowed"),
    (re.compile(r"\bfile\.remove\b", re.I), "file.remove is not allowed"),
    (re.compile(r"\bunlink\s*\(", re.I), "unlink is not allowed"),
    (re.compile(r"\bsetwd\s*\(", re.I), "setwd is not allowed"),
    (re.compile(r"\bfile\.copy\b", re.I), "file.copy is not allowed"),
    (re.compile(r"\bfile\.rename\b", re.I), "file.rename is not allowed"),
    (re.compile(r"\bdir\.create\b", re.I), "dir.create is not allowed"),
    (re.compile(r"\bsink\s*\(", re.I), "sink is not allowed"),
    (re.compile(r"\bparallel::", re.I), "parallel is not allowed"),
    (re.compile(r"\bfuture::", re.I), "future is not allowed"),
]


def guard_r_code(code: str) -> str | None:
    """Return an error message if the snippet is disallowed, else None."""
    text = code.strip()
    if not text:
        return "R code is empty"
    if len(text) > MAX_CODE_CHARS:
        return f"R code exceeds {MAX_CODE_CHARS} characters"
    for pattern, message in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return message
    return None


# Workspace scripts may write files inside the project, so the write-family
# blocks above are dropped; everything that escapes the workspace sandbox
# (network, package installation, shell, process spawning) stays blocked.
_SCRIPT_BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdownload\.file\b", re.I), "download.file is not allowed"),
    (re.compile(r"\burl\s*\(", re.I), "url() is not allowed"),
    (re.compile(r"\bcurl\b", re.I), "curl is not allowed"),
    (re.compile(r"\bhttr\b", re.I), "httr is not allowed"),
    (re.compile(r"\bhttr2\b", re.I), "httr2 is not allowed"),
    (re.compile(r"\brvest\b", re.I), "rvest is not allowed"),
    (re.compile(r"\bsystem2?\s*\(", re.I), "system/system2 is not allowed"),
    (re.compile(r"\binstall\.packages\b", re.I), "install.packages is not allowed"),
    (re.compile(r"\bbiocmanager::install\b", re.I), "package installation is not allowed"),
    (re.compile(r"\bdevtools::", re.I), "devtools is not allowed"),
    (re.compile(r"\bremotes::", re.I), "remotes is not allowed"),
    (re.compile(r"\bpak::", re.I), "pak is not allowed"),
    (re.compile(r"\bsys\.exec\b", re.I), "process execution is not allowed"),
    (re.compile(r"\bprocessx::", re.I), "processx is not allowed"),
    (re.compile(r"\bparallel::", re.I), "parallel is not allowed"),
    (re.compile(r"\bfuture::", re.I), "future is not allowed"),
    (re.compile(r"\bsetwd\s*\(", re.I), "setwd is not allowed; scripts already run with the project directory as the working directory"),
]


def guard_r_script(code: str) -> str | None:
    """Guard a workspace-scoped R script: writes allowed, escapes blocked."""
    text = code.strip()
    if not text:
        return "R script is empty"
    for pattern, message in _SCRIPT_BLOCKED_PATTERNS:
        if pattern.search(text):
            return message
    return None


def run_r_inspect(
    code: str,
    *,
    cwd: Path | str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a short read-oriented R snippet and return a structured observation."""
    blocked = guard_r_code(code)
    if blocked:
        return {"status": "error", "error": blocked}

    work_dir = Path(cwd) if cwd else None
    cleanup_dir: Path | None = None
    if work_dir is None or not work_dir.exists():
        cleanup_dir = Path(tempfile.mkdtemp(prefix="omicsbase-run-r-"))
        work_dir = cleanup_dir

    snippet_path = work_dir / ".omicsbase_run_r_snippet.R"
    harness_path = work_dir / ".omicsbase_run_r_harness.R"
    result_path = work_dir / ".omicsbase_run_r_result.json"

    try:
        snippet_path.write_text(code.strip() + "\n", encoding="utf-8")
        harness_path.write_text(_HARNESS_R, encoding="utf-8")
        from app.services.runner import run_command_sync

        success, run_output = run_command_sync(
            ["Rscript", str(harness_path.name)],
            cwd=str(work_dir),
            timeout=timeout_s,
        )
        payload = _load_result(result_path)
        stdout = _trim((payload.get("stdout") if payload else None) or run_output or "")
        stderr = _trim("" if success else run_output)
        error = None
        if payload and payload.get("error"):
            error = str(payload["error"])
        elif not success and not payload:
            error = _trim(stderr or stdout or "Rscript failed")

        summary = (payload or {}).get("summary")
        status = "ok" if error is None else "error"
        observation: dict[str, Any] = {
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "summary": summary,
        }
        if error:
            observation["error"] = error
        return _trim_observation(observation)
    except FileNotFoundError:
        return {"status": "error", "error": "Rscript is not available in this environment"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"R inspection timed out after {timeout_s} seconds"}
    except Exception as exc:
        logger.exception("run_r_inspect failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        for path in (snippet_path, harness_path, result_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if cleanup_dir is not None:
            try:
                cleanup_dir.rmdir()
            except OSError:
                pass


def _load_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _trim(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...[truncated]..."


def _trim_observation(observation: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(observation, default=str)
    if len(encoded) <= MAX_OUTPUT_CHARS:
        return observation
    # Prefer keeping summary + error; truncate stdout/stderr first.
    observation = dict(observation)
    observation["stdout"] = _trim(str(observation.get("stdout") or ""), 2000)
    observation["stderr"] = _trim(str(observation.get("stderr") or ""), 1000)
    summary = observation.get("summary")
    if isinstance(summary, dict) and "head" in summary:
        summary = dict(summary)
        summary["head"] = (summary.get("head") or [])[:3]
        observation["summary"] = summary
    return observation


_HARNESS_R = r"""
options(warn = 1)
snippet <- ".omicsbase_run_r_snippet.R"
out_path <- ".omicsbase_run_r_result.json"

json_escape <- function(x) {
  x <- enc2utf8(as.character(x))
  x <- gsub("\\\\", "\\\\\\\\", x, useBytes = TRUE)
  x <- gsub("\"", "\\\\\"", x, useBytes = TRUE)
  x <- gsub("\n", "\\\\n", x, useBytes = TRUE)
  x <- gsub("\r", "\\\\r", x, useBytes = TRUE)
  x <- gsub("\t", "\\\\t", x, useBytes = TRUE)
  x
}

to_json_string <- function(x) paste0("\"", json_escape(x), "\"")

to_json_string_array <- function(values, limit = 80) {
  values <- as.character(values)
  if (length(values) > limit) {
    values <- c(values[seq_len(limit)], paste0("...+", length(values) - limit, " more"))
  }
  paste0("[", paste(vapply(values, to_json_string, character(1)), collapse = ","), "]")
}

summarize_value <- function(x) {
  if (is.null(x)) return("null")
  cls <- paste(class(x), collapse = "/")
  if (is.data.frame(x) || is.matrix(x)) {
    dims <- dim(x)
    cn <- colnames(x)
    if (is.null(cn)) cn <- character(0)
    rn <- rownames(x)
    if (is.null(rn)) rn <- character(0)
    head_n <- min(5L, nrow(x))
    head_rows <- character(0)
    if (head_n > 0) {
      for (i in seq_len(head_n)) {
        row <- as.character(unlist(x[i, , drop = TRUE], use.names = FALSE))
        row[is.na(row)] <- ""
        if (length(row) > 20) row <- c(row[1:20], "...")
        head_rows <- c(head_rows, paste(row, collapse = " | "))
      }
    }
    return(paste0(
      "{",
      "\"class\":", to_json_string(cls), ",",
      "\"rows\":", dims[[1]], ",",
      "\"columns\":", dims[[2]], ",",
      "\"colnames\":", to_json_string_array(cn), ",",
      "\"rownames_sample\":", to_json_string_array(head(rn, 10)), ",",
      "\"head\":", to_json_string_array(head_rows, limit = 5),
      "}"
    ))
  }
  if (is.list(x) && !is.null(names(x))) {
    return(paste0(
      "{",
      "\"class\":", to_json_string(cls), ",",
      "\"names\":", to_json_string_array(names(x), limit = 60),
      "}"
    ))
  }
  if (is.atomic(x)) {
    return(paste0(
      "{",
      "\"class\":", to_json_string(cls), ",",
      "\"length\":", length(x), ",",
      "\"preview\":", to_json_string_array(head(as.character(x), 20), limit = 20),
      "}"
    ))
  }
  paste0("{\"class\":", to_json_string(cls), "}")
}

stdout_txt <- character(0)
err <- NULL
value <- NULL
tryCatch({
  code_text <- paste(readLines(snippet, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
  stdout_txt <<- capture.output({
    value <<- eval(parse(text = code_text), envir = new.env(parent = globalenv()))
  }, type = "output")
}, error = function(e) {
  err <<- conditionMessage(e)
})

stdout_joined <- paste(stdout_txt, collapse = "\n")
summary_json <- summarize_value(value)
error_json <- if (is.null(err)) "null" else to_json_string(err)

json <- paste0(
  "{",
  "\"stdout\":", to_json_string(stdout_joined), ",",
  "\"error\":", error_json, ",",
  "\"summary\":", summary_json,
  "}"
)
writeLines(json, out_path, useBytes = TRUE)
if (!is.null(err)) quit(status = 1)
"""
