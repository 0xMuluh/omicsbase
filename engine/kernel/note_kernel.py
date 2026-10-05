"""
Persistent NoteKernel for OmicsBase 3.
Adapted from OmicsBase v1 note_kernel.py.
Maintains one persistent R process per NoteThread / study scope so variables
stay in memory across conversation turns without costly re-serialization.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("note_kernel")

KERNEL_DIR_REL = Path(".omicsbase") / "note-kernel"
KERNEL_SCRIPT_NAME = "kernel.R"
REQUEST_FILE_NAME = "request.json"
PID_FILE_NAME = "kernel.pid"
START_LOCK_NAME = "start.lock"
DONE_PREFIX = "done-"
KERNEL_LOG_NAME = "kernel.log"
EXECUTE_LOCK_NAME = "execute.lock"
POLL_INTERVAL = 0.1
SHUTDOWN_WAIT_SECONDS = 5.0

# A session idle this long is saved and stopped; its next cell restores it.
IDLE_SECONDS = int(os.environ.get("NOTE_IDLE_SECONDS", "1200"))
# Live R sessions at most. A new one first stops the least recently used idle
# session; if all are busy it waits up to QUEUE_SECONDS for one to free up.
MAX_SESSIONS = int(os.environ.get("NOTE_MAX_SESSIONS", "8"))
QUEUE_SECONDS = int(os.environ.get("NOTE_QUEUE_SECONDS", "120"))
# Time a stopping session gets to save its workspace before it is killed.
SAVE_WAIT_SECONDS = float(os.environ.get("NOTE_SAVE_WAIT_SECONDS", "120"))
REAP_INTERVAL_SECONDS = 60
# Console text one cell may write to disk.
CONSOLE_MAX_BYTES = int(os.environ.get("NOTE_CONSOLE_MAX_BYTES", str(5_000_000)))

WORKSPACE_RELATIVE_PATH = Path(".omicsbase") / "note-kernel" / "workspace.RData"
WORKSPACE_OBJECTS_RELATIVE_PATH = Path(".omicsbase") / "note-kernel" / "workspace-objects.txt"
CONSOLE_FILE_NAME = ".note_console.txt"
EVENTS_FILE_NAME = ".note_events.jsonl"

_R_DEFAULT_ATTACHED = ("base", "stats", "graphics", "grDevices", "utils", "datasets", "methods")

_KERNEL_SCRIPT_TEMPLATE = r"""
.note_t0 <- proc.time()
.note_t_load <- 0
ws <- {ws_rel_q}
objects_file <- {objects_rel_q}
defaults <- c({defaults})
# Told to the note in its first cell, so a restart is never silent.
.note_restore_msg <- ''
if (file.exists(ws)) .note_restore_msg <- tryCatch({{
  .note_loaded <- load(ws, envir = .GlobalEnv)
  sprintf('[note] This note\'s R session was restarted; %d saved objects were restored.\n',
    sum(!startsWith(.note_loaded, '.') & !.note_loaded %in% c('ws', 'objects_file', 'defaults')))
}}, error = function(e) {{
  cat('[note] workspace load failed:', conditionMessage(e), '\n')
  paste0('[note] This note\'s R session was restarted and its saved objects could not be restored: ',
    conditionMessage(e), '\n')
}})
.note_attached <- tryCatch(get('.note_attached_packages', envir = .GlobalEnv),
  error = function(e) character())
for (.note_p in .note_attached) tryCatch(
  suppressPackageStartupMessages(suppressWarnings(require(.note_p, character.only = TRUE))),
  error = function(e) NULL)
.note_t_load <- (proc.time() - .note_t0)[['elapsed']]

tryCatch({{
  .disarm_fn <- function(name) {{
    function(...) {{
      stop(paste0("Dynamic package installation via '", name, "' is disabled in OmicsBase. All analysis libraries are pre-compiled into the container."))
    }}
  }}
  if (exists('install.packages', envir = asNamespace('utils'))) {{
    utils::assignInNamespace('install.packages', .disarm_fn('install.packages'), ns = 'utils')
  }}
}}, error = function(e) NULL)

.note_state <- new.env(parent = emptyenv())

.note_console_max <- {console_max_bytes}
# Writes console text up to the per-cell limit and returns what was kept.
.note_log <- function(x) {{
  if (.note_state$truncated) return('')
  x <- paste0(x, collapse = '')
  room <- .note_console_max - .note_state$bytes
  if (nchar(x, type = 'bytes') > room) {{
    x <- paste0(substr(x, 1L, max(0L, room)), sprintf(
      '\n[output truncated: this cell printed more than %d MB]\n', .note_console_max %/% 1e6))
    .note_state$truncated <- TRUE
  }}
  .note_state$bytes <- .note_state$bytes + nchar(x, type = 'bytes')
  cat(x, file = .note_state$con, sep = '')
  x
}}
.note_text <- function(x) {{
  kept <- .note_log(x)
  if (nzchar(kept)) .note_emit('text', content = kept)
  invisible(NULL)
}}
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
  .note_state$bytes <- 0
  .note_state$truncated <- FALSE
  .note_state$capture_plots <- {capture_plots}
  .note_state$quiet <- {quiet}
  dir.create(.note_state$plots_dir, showWarnings = FALSE, recursive = TRUE)
  dir.create(.note_state$tables_dir, showWarnings = FALSE, recursive = TRUE)
  file.create(file.path(run_dir, {console_name_q}))
  file.create(file.path(run_dir, {events_name_q}))
  .note_state$con <- file(file.path(run_dir, {console_name_q}), open = 'a')
  .note_state$ev_con <- file(file.path(run_dir, {events_name_q}), open = 'a')
  if (nzchar(.note_restore_msg)) {{
    .note_log(.note_restore_msg)
    .note_restore_msg <<- ''
  }}
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
          text = function(x) .note_text(x),
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
              .note_text(paste0(paste0(capture.output(print(x)), collapse = '\n'), '\n'))
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
      # Save beside the old workspace and swap, so an interrupted save keeps the last one.
      save.image(file = paste0(ws, '.tmp'))
      file.rename(paste0(ws, '.tmp'), ws)
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
    quiet_package_startup: bool = True,
    capture_plots: bool = True,
) -> str:
    """Render the persistent kernel R script for one thread scope."""
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
        console_max_bytes=CONSOLE_MAX_BYTES,
    )


class KernelCancelled(Exception):
    pass


class KernelTimeout(Exception):
    pass


class KernelDied(Exception):
    pass


class KernelBusy(Exception):
    pass


class KernelHandle:
    __slots__ = ("project_dir", "pid", "proc", "started_at", "last_used")

    def __init__(self, project_dir: str, pid: int, proc: subprocess.Popen | None = None):
        now = time.time()
        self.project_dir = project_dir
        self.pid = pid
        # Set when this process started the kernel; see _alive.
        self.proc = proc
        self.started_at = now
        self.last_used = now


_kernels: dict[str, KernelHandle] = {}
_registry_lock = threading.Lock()


def _kernel_root(project_dir: str) -> Path:
    return Path(project_dir).resolve() / KERNEL_DIR_REL


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        state = stat.rsplit(")", 1)[1].split()[0] if ")" in stat else ""
        if state == "Z":
            return False
        cmdline = Path(f"/proc/{pid}/cmdline").read_text()
        return "R" in cmdline or "Rscript" in cmdline
    except (OSError, IndexError):
        return True


def _alive(handle: KernelHandle) -> bool:
    """Whether the handle's kernel is running. A kernel this process started is asked
    directly: between fork and exec its command line is not yet Rscript's."""
    if handle.proc is not None:
        return handle.proc.poll() is None
    return _pid_alive(handle.pid)


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
    root = _kernel_root(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    script = root / KERNEL_SCRIPT_NAME
    script.write_text(
        build_kernel_script(
            quiet_package_startup=True,
            capture_plots=True,
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
    handle = KernelHandle(project_dir, proc.pid, proc)
    logger.info("note kernel started pid=%s scope=%s", proc.pid, project_dir)
    return handle


def ensure_kernel(project_dir: str, ttl_seconds: int = IDLE_SECONDS) -> KernelHandle:
    """Return a live kernel for the scope, starting or reusing one."""
    root = _kernel_root(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / START_LOCK_NAME

    with open(lock_path, "a+") as lock_handle:
        import fcntl

        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            handle = _kernels.get(project_dir)
            now = time.time()
            if handle and _alive(handle):
                if now - handle.last_used <= ttl_seconds:
                    handle.last_used = now
                    return handle
                shutdown_kernel(handle, SAVE_WAIT_SECONDS)

            pid_file = root / PID_FILE_NAME
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    if _pid_alive(pid):
                        handle = KernelHandle(project_dir, pid)
                        with _registry_lock:
                            _kernels[project_dir] = handle
                        return handle
                except (ValueError, OSError):
                    pass

            _make_room(project_dir)
            handle = start_kernel(project_dir)
            with _registry_lock:
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
    timeout_seconds: int = 180,
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
            handle.last_used = time.time()
            return done
        if not _alive(handle):
            with _registry_lock:
                _kernels.pop(handle.project_dir, None)
            raise KernelDied("The R kernel process died")
        time.sleep(POLL_INTERVAL)

    kill_kernel(handle)
    raise KernelTimeout("Cell execution exceeded its timeout")


def shutdown_kernel(handle: KernelHandle, wait_seconds: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Ask the kernel to save its workspace and exit; kill if it lingers."""
    if not _alive(handle):
        with _registry_lock:
            _kernels.pop(handle.project_dir, None)
        return
    root = _kernel_root(handle.project_dir)
    try:
        _write_atomic(
            root / REQUEST_FILE_NAME,
            json.dumps({"id": f"shutdown-{int(time.time() * 1000)}", "shutdown": True}),
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and _alive(handle):
            time.sleep(POLL_INTERVAL)
    finally:
        kill_kernel(handle)


def kill_kernel(handle: KernelHandle) -> None:
    with _registry_lock:
        _kernels.pop(handle.project_dir, None)
    if not _alive(handle):
        return
    try:
        os.kill(handle.pid, 15)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _alive(handle):
            time.sleep(0.05)
        if _alive(handle):
            os.kill(handle.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    if handle.proc is not None:
        try:
            handle.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


def _try_lock_idle(handle: KernelHandle):
    """The note's execute lock if no cell is running, else None. Held, no cell can start."""
    lock = open(_kernel_root(handle.project_dir) / EXECUTE_LOCK_NAME, "a+")
    import fcntl

    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def _stop_if_idle(handle: KernelHandle, idle_for: float = 0) -> bool:
    """Save and stop a session that has no cell running and has been idle long enough."""
    lock = _try_lock_idle(handle)
    if lock is None:
        return False
    try:
        if time.time() - handle.last_used < idle_for:
            return False
        logger.info(
            "note kernel stopping pid=%s idle=%ds scope=%s",
            handle.pid, time.time() - handle.last_used, handle.project_dir,
        )
        shutdown_kernel(handle, SAVE_WAIT_SECONDS)
        return True
    finally:
        lock.close()


def _live_kernels(exclude: str | None = None) -> list[KernelHandle]:
    with _registry_lock:
        handles = list(_kernels.values())
    live = []
    for handle in handles:
        if not _alive(handle):
            with _registry_lock:
                if _kernels.get(handle.project_dir) is handle:
                    _kernels.pop(handle.project_dir)
        elif handle.project_dir != exclude:
            live.append(handle)
    return live


def _make_room(project_dir: str) -> None:
    """Keep live sessions under MAX_SESSIONS, stopping the least recently used idle one."""
    deadline = time.monotonic() + QUEUE_SECONDS
    waited = False
    while True:
        live = _live_kernels(exclude=project_dir)
        if len(live) < MAX_SESSIONS:
            if waited:
                logger.info("note kernel slot freed for scope=%s", project_dir)
            return
        if any(_stop_if_idle(h) for h in sorted(live, key=lambda h: h.last_used)):
            continue
        if time.monotonic() >= deadline:
            raise KernelBusy(
                f"All {MAX_SESSIONS} R sessions are busy running other notes' cells. "
                "Try again in a few minutes."
            )
        if not waited:
            logger.warning("note kernel limit reached (%d busy); queueing scope=%s", len(live), project_dir)
            waited = True
        time.sleep(1.0)


def reap_idle_kernels() -> int:
    """Stop every session idle for longer than IDLE_SECONDS. Returns how many were stopped."""
    now = time.time()
    stopped = 0
    for handle in _live_kernels():
        if now - handle.last_used > IDLE_SECONDS and _stop_if_idle(handle, IDLE_SECONDS):
            stopped += 1
    return stopped


def start_reaper() -> threading.Thread:
    """Run reap_idle_kernels every REAP_INTERVAL_SECONDS in a daemon thread."""

    def loop() -> None:
        while True:
            time.sleep(REAP_INTERVAL_SECONDS)
            try:
                reap_idle_kernels()
            except Exception:
                logger.exception("note kernel reaper failed")

    thread = threading.Thread(target=loop, name="note-kernel-reaper", daemon=True)
    thread.start()
    logger.info(
        "note kernel reaper started: idle=%ss max_sessions=%s", IDLE_SECONDS, MAX_SESSIONS
    )
    return thread
