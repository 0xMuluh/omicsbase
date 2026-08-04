"""Persistent R kernel: one long-lived Rscript process per thread scope.

A cell in a NoteThread runs in a fresh Rscript subprocess and carries
state by serializing the whole workspace after every cell
(``save.image``), which measures ~1s of every 2.1s cell. The kernel
keeps one R process alive per thread: state lives in memory, the
workspace is loaded once at startup and saved only on idle teardown or
shutdown, and each cell is a request handled by the running process.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

KERNEL_DIR_REL = Path(".omicsbase") / "note-kernel"
KERNEL_SCRIPT_NAME = "kernel.R"
REQUEST_FILE_NAME = "request.json"
PID_FILE_NAME = "kernel.pid"
START_LOCK_NAME = "start.lock"
DONE_PREFIX = "done-"
KERNEL_LOG_NAME = "kernel.log"
POLL_INTERVAL = 0.1
SHUTDOWN_WAIT_SECONDS = 5.0

_R_DEFAULT_ATTACHED = ("base", "stats", "graphics", "grDevices", "utils", "datasets", "methods")

# Mirrors the one-shot evaluate harness in note_execution._evaluate_driver;
# keep the two capture behaviours in sync.
_KERNEL_SCRIPT_TEMPLATE = r"""
.note_t0 <- proc.time()
.note_t_load <- 0
ws <- {ws_rel_q}
objects_file <- {objects_rel_q}
defaults <- c({defaults})
if (file.exists(ws)) tryCatch(load(ws, envir = .GlobalEnv),
  error = function(e) cat('[note] workspace load failed:', conditionMessage(e), '\n'))
.note_attached <- tryCatch(get('.note_attached_packages', envir = .GlobalEnv),
  error = function(e) character())
for (.note_p in .note_attached) tryCatch(
  suppressPackageStartupMessages(suppressWarnings(require(.note_p, character.only = TRUE))),
  error = function(e) NULL)
.note_t_load <- (proc.time() - .note_t0)[['elapsed']]

.note_state <- new.env(parent = emptyenv())

.note_log <- function(x) {{ cat(x, file = .note_state$con, sep = ''); invisible(NULL) }}
.note_emit <- function(type, ..., .path = NULL, .rows = NULL, .cols = NULL) {{
  .note_state$seq <- .note_state$seq + 1L
  rec <- c(list(seq = .note_state$seq, type = type), list(...))
  if (!is.null(.path)) rec$path <- .path
  if (!is.null(.rows)) rec$rows <- .rows
  if (!is.null(.cols)) rec$cols <- .cols
  writeLines(jsonlite::toJSON(rec, auto_unbox = TRUE, null = 'null'), .note_state$ev_con)
  invisible(NULL)
}}
.note_display <- function(x, ...) {{
  if (is.data.frame(x) && ncol(x) > 0L) {{
    .note_state$tables <- .note_state$tables + 1L
    f <- file.path(.note_state$tables_dir, sprintf('table_%03d.csv', .note_state$tables))
    write.csv(as.data.frame(x), f, row.names = FALSE)
    .note_log(sprintf('[table: %d rows x %d cols]\n', nrow(x), ncol(x)))
    .note_emit('table', .path = f, .rows = nrow(x), .cols = ncol(x))
    invisible(x)
  }} else {{
    base::print(x, ...)
  }}
}}

.note_run <- function(run_dir, source) {{
  .note_state$plots_dir <- file.path(run_dir, 'plots')
  .note_state$tables_dir <- file.path(run_dir, 'tables')
  .note_state$seq <- 0L
  .note_state$plots <- 0L
  .note_state$tables <- 0L
  .note_state$capture_plots <- {capture_plots}
  .note_state$quiet <- {quiet}
  dir.create(.note_state$plots_dir, showWarnings = FALSE, recursive = TRUE)
  dir.create(.note_state$tables_dir, showWarnings = FALSE, recursive = TRUE)
  file.create(file.path(run_dir, {console_name_q}))
  file.create(file.path(run_dir, {events_name_q}))
  .note_state$con <- file(file.path(run_dir, {console_name_q}), open = 'a')
  .note_state$ev_con <- file(file.path(run_dir, {events_name_q}), open = 'a')
  .note_t1 <- proc.time()
  .note_source <- tryCatch({{
    .note_parsed <- parse(text = source)
    paste(vapply(as.list(.note_parsed), function(e) {{
      if (is.call(e) && identical(e[[1L]], as.name('print')) && length(e) >= 2L) e[[1L]] <- as.name('.note_display')
      paste(deparse(e, width.cutoff = 500L), collapse = '\n')
    }}, character(1)), collapse = '\n')
  }}, error = function(e) e)
  tryCatch({{
    if (inherits(.note_source, 'error')) {{
      .note_log(paste0('Error: ', conditionMessage(.note_source), '\n'))
      .note_emit('error', content = conditionMessage(.note_source))
    }} else {{
      evaluate::evaluate(.note_source, envir = .GlobalEnv, stop_on_error = 0L,
        output_handler = evaluate::new_output_handler(
          text = function(x) {{ .note_log(x); .note_emit('text', content = x); invisible(NULL) }},
          message = function(cond) {{
            if (.note_state$quiet && inherits(cond, 'packageStartupMessage')) return(invisible(NULL))
            .note_log(paste0(conditionMessage(cond), '\n'))
            invisible(NULL)
          }},
          warning = function(cond) {{
            .note_log(paste0('Warning: ', conditionMessage(cond), '\n'))
            .note_emit('warning', content = conditionMessage(cond))
            invisible(NULL)
          }},
          error = function(cond) {{
            .note_log(paste0('Error: ', conditionMessage(cond), '\n'))
            .note_emit('error', content = conditionMessage(cond))
            invisible(NULL)
          }},
          value = function(x, visible) {{
            if (!visible) return(invisible(NULL))
            if (is.data.frame(x) && ncol(x) > 0L) {{
              .note_display(x)
            }} else {{
              rendered <- paste0(capture.output(print(x)), collapse = '\n')
              .note_log(rendered)
              .note_log('\n')
              .note_emit('text', content = paste0(rendered, '\n'))
            }}
            invisible(NULL)
          }},
          graphics = function(recordedplot) {{
            if (.note_state$capture_plots) {{
              .note_state$plots <- .note_state$plots + 1L
              f <- file.path(.note_state$plots_dir, sprintf('plot_%03d.png', .note_state$plots))
              png(f, width = 800, height = 600, res = 110)
              tryCatch({{ evaluate::replay(recordedplot) }}, finally = dev.off())
              .note_emit('plot', .path = f)
            }} else {{
              pdf(NULL)
              tryCatch({{ evaluate::replay(recordedplot) }}, finally = dev.off())
            }}
            invisible(NULL)
          }}
        )
      )
    }}
  }}, finally = {{
    close(.note_state$con)
    close(.note_state$ev_con)
    .note_timing <- list(
      total_seconds = (proc.time() - .note_t0)[['elapsed']],
      load_seconds = .note_t_load,
      eval_seconds = (proc.time() - .note_t1)[['elapsed']],
      save_seconds = 0)
    tryCatch(
      writeLines(jsonlite::toJSON(.note_timing, auto_unbox = TRUE, digits = 6),
        file.path(run_dir, 'timing.json')),
      error = function(e) NULL)
  }})
  invisible(NULL)
}}

.note_request_file <- {request_q}
.note_done_dir <- {done_dir_q}
repeat {{
  Sys.sleep({poll_interval})
  if (!file.exists(.note_request_file)) next
  .note_req <- tryCatch(jsonlite::fromJSON(.note_request_file), error = function(e) NULL)
  if (is.null(.note_req) || is.null(.note_req$id)) next
  file.remove(.note_request_file)
  .note_id <- .note_req$id
  if (isTRUE(.note_req$shutdown)) {{
    tryCatch({{
      assign('.note_attached_packages', setdiff(.packages(), defaults), envir = .GlobalEnv)
      save.image(file = ws)
      dir.create(dirname(objects_file), showWarnings = FALSE, recursive = TRUE)
      writeLines(sort(ls(.GlobalEnv, all.names = TRUE)), objects_file)
    }}, error = function(e) cat('[note] workspace save failed:', conditionMessage(e), '\n'))
    quit(save = 'no', status = 0)
  }}
  tryCatch(.note_run(.note_req$run_dir, .note_req$source),
    error = function(e) cat('[note] kernel request failed:', conditionMessage(e), '\n'))
  tryCatch(
    writeLines(jsonlite::toJSON(list(id = .note_id, status = 'ok'), auto_unbox = TRUE),
      file.path(.note_done_dir, paste0({done_prefix_q}, .note_id, '.json'))),
    error = function(e) NULL)
}}
"""


def build_kernel_script(
    *,
    quiet_package_startup: bool,
    capture_plots: bool,
) -> str:
    """Render the persistent kernel R script for one thread scope."""
    from app.services.note_execution import (
        CONSOLE_FILE_NAME,
        EVENTS_FILE_NAME,
        WORKSPACE_OBJECTS_RELATIVE_PATH,
        WORKSPACE_RELATIVE_PATH,
    )

    q = json.dumps
    return _KERNEL_SCRIPT_TEMPLATE.format(
        ws_rel_q=q(WORKSPACE_RELATIVE_PATH.as_posix()),
        objects_rel_q=q(WORKSPACE_OBJECTS_RELATIVE_PATH.as_posix()),
        defaults=", ".join(repr(item) for item in _R_DEFAULT_ATTACHED),
        console_name_q=q(CONSOLE_FILE_NAME),
        events_name_q=q(EVENTS_FILE_NAME),
        capture_plots="TRUE" if capture_plots else "FALSE",
        quiet="TRUE" if quiet_package_startup else "FALSE",
        request_q=q((KERNEL_DIR_REL / REQUEST_FILE_NAME).as_posix()),
        done_dir_q=q(KERNEL_DIR_REL.as_posix()),
        done_prefix_q=q(DONE_PREFIX),
        poll_interval=POLL_INTERVAL,
    )


class KernelCancelled(Exception):
    pass


class KernelTimeout(Exception):
    pass


class KernelDied(Exception):
    pass


class KernelHandle:
    __slots__ = ("project_dir", "pid", "started_at", "last_used")

    def __init__(self, project_dir: str, pid: int):
        now = time.time()
        self.project_dir = project_dir
        self.pid = pid
        self.started_at = now
        self.last_used = now


_kernels: dict[str, KernelHandle] = {}


def _kernel_root(project_dir: str) -> Path:
    return Path(project_dir).resolve() / KERNEL_DIR_REL


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    # Zombies answer os.kill(pid, 0) but will never serve a request.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        state = stat.rsplit(")", 1)[1].split()[0] if ")" in stat else ""
        return state != "Z"
    except (OSError, IndexError):
        return True


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_done(root: Path, request_id: str) -> dict[str, Any] | None:
    path = root / f"{DONE_PREFIX}{request_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        payload = {"status": "ok"}
    try:
        path.unlink()
    except OSError:
        pass
    return payload


def start_kernel(project_dir: str) -> KernelHandle:
    """Launch the kernel R process for a thread scope."""
    from app.config import settings

    root = _kernel_root(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    script = root / KERNEL_SCRIPT_NAME
    script.write_text(
        build_kernel_script(
            quiet_package_startup=bool(settings.note_execution_quiet_package_startup),
            capture_plots=bool(settings.note_execution_capture_plots),
        ),
        encoding="utf-8",
    )
    log_handle = open(root / KERNEL_LOG_NAME, "ab")
    proc = subprocess.Popen(
        ["Rscript", "--vanilla", (KERNEL_DIR_REL / KERNEL_SCRIPT_NAME).as_posix()],
        cwd=project_dir,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    _write_atomic(root / PID_FILE_NAME, str(proc.pid))
    handle = KernelHandle(project_dir, proc.pid)
    logger.info("note kernel started pid=%s scope=%s", proc.pid, project_dir)
    return handle


def ensure_kernel(project_dir: str) -> KernelHandle:
    """Return a live kernel for the scope, starting or reusing one.

    Cross-process safe: a start lock plus the pid file lets workers share
    the kernel a thread already has. Idle kernels are shut down (saving the
    workspace) before a fresh one starts.
    """
    from app.config import settings

    root = _kernel_root(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / START_LOCK_NAME
    ttl = max(60, int(settings.note_kernel_idle_ttl_seconds or 1800))

    with open(lock_path, "a+") as lock_handle:
        import fcntl

        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            handle = _kernels.get(project_dir)
            now = time.time()
            if handle and _pid_alive(handle.pid):
                if now - handle.last_used <= ttl:
                    handle.last_used = now
                    return handle
                shutdown_kernel(handle)

            pid_file = root / PID_FILE_NAME
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    if _pid_alive(pid):
                        handle = KernelHandle(project_dir, pid)
                        _kernels[project_dir] = handle
                        handle.last_used = now
                        return handle
                except (ValueError, OSError):
                    pass

            handle = start_kernel(project_dir)
            _kernels[project_dir] = handle
            return handle
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def request_cell(
    handle: KernelHandle,
    *,
    request_id: str,
    run_dir_rel: str,
    source: str,
    timeout_seconds: int,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Send one cell to the kernel and wait for its done marker."""
    root = _kernel_root(handle.project_dir)
    _write_atomic(
        root / REQUEST_FILE_NAME,
        json.dumps({"id": request_id, "run_dir": run_dir_rel, "source": source}),
    )
    handle.last_used = time.time()
    deadline = time.monotonic() + max(1, int(timeout_seconds)) + 5

    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            kill_kernel(handle)
            raise KernelCancelled("Cell execution cancelled")
        done = _read_done(root, request_id)
        if done is not None:
            return done
        if not _pid_alive(handle.pid):
            _kernels.pop(handle.project_dir, None)
            raise KernelDied("The R kernel process died")
        time.sleep(POLL_INTERVAL)

    kill_kernel(handle)
    raise KernelTimeout("Cell execution exceeded its timeout")


def shutdown_kernel(handle: KernelHandle) -> None:
    """Ask the kernel to save its workspace and exit; kill if it lingers."""
    if not _pid_alive(handle.pid):
        _kernels.pop(handle.project_dir, None)
        return
    root = _kernel_root(handle.project_dir)
    try:
        _write_atomic(
            root / REQUEST_FILE_NAME,
            json.dumps({"id": f"shutdown-{int(time.time() * 1000)}", "shutdown": True}),
        )
        deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
        while time.monotonic() < deadline and _pid_alive(handle.pid):
            time.sleep(POLL_INTERVAL)
    finally:
        kill_kernel(handle)


def kill_kernel(handle: KernelHandle) -> None:
    _kernels.pop(handle.project_dir, None)
    if not _pid_alive(handle.pid):
        return
    try:
        os.kill(handle.pid, 15)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _pid_alive(handle.pid):
            time.sleep(0.05)
        if _pid_alive(handle.pid):
            os.kill(handle.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
