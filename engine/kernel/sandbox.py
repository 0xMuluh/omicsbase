"""
Restrictions for every R session, Quarto render and R helper the engine starts.

The engine starts the command through this file, run as a script: a fresh process that
restricts itself and then execs the command, so nothing runs between fork and exec in
the multi-threaded engine. The command gets a minimal environment, so the engine's
secrets never reach a note, and a private HOME and TMPDIR inside its folder. On Linux,
Landlock then limits its files to that folder, the shared download caches and read-only
system paths, and stops it from signalling processes outside itself. The kernel also
kills it before the engine when memory runs out.

NOTE_SANDBOX=auto (default) applies Landlock where the kernel offers it and warns
otherwise; required refuses to start without it; off skips it. The environment is
always minimal. Only the standard library is imported, so the script runs with -I.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("note_kernel")

MODE = os.environ.get("NOTE_SANDBOX", "auto")

# Variables R may see. Anything else from the engine's environment is left out.
_ENV_NAMES = {
    "PATH", "LANG", "LANGUAGE", "TZ",
    "EXPERIMENT_HUB_CACHE", "ANNOTATION_HUB_CACHE", "XDG_CACHE_HOME",
    "RETICULATE_PYTHON", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
}
_ENV_PREFIXES = ("LC_", "R_", "BIOCONDUCTOR_")
# Never passed, even when the name is allowed above (R_ also matches Kubernetes service
# variables of a service whose name starts with "r-").
_ENV_DENIED = re.compile("SECRET|TOKEN|KEY|PASSWORD|KUBERNETES|_SERVICE_|_PORT", re.IGNORECASE)
# The engine image's Python (engine/Dockerfile), which reticulate uses.
_VENV_PYTHON = "/opt/venv/bin/python"
# Download caches the session may write to (shared between notes).
_CACHE_VARS = ("EXPERIMENT_HUB_CACHE", "ANNOTATION_HUB_CACHE", "R_USER_CACHE_DIR", "XDG_CACHE_HOME")

# Read-only: programs, libraries, R and its packages (/usr/local/lib/R in the image),
# Quarto (/opt/quarto), configuration, certificates and DNS (/etc), the font cache.
_SYSTEM_PATHS = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/libx32", "/etc", "/opt",
    "/var/cache/fontconfig",
)
_R_PATH_VARS = ("R_HOME", "R_LIBS", "R_LIBS_SITE", "R_LIBS_USER")
_DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")

# Landlock (linux/landlock.h).
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
_SCOPE_ABSTRACT_UNIX_SOCKET, _SCOPE_SIGNAL = 1 << 0, 1 << 1
_READ = EXECUTE | READ_FILE | READ_DIR
_DEVICE = READ_FILE | WRITE_FILE | TRUNCATE | IOCTL_DEV


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


# Packed in the kernel (12 bytes); unpacked here only adds padding after parent_fd.
class _PathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


def _syscall(number: int, *args) -> int:
    result = _libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


class SandboxUnavailable(RuntimeError):
    pass


@functools.cache
def landlock_abi() -> int:
    """The kernel's Landlock version, or 0 when it has none."""
    if sys.platform != "linux":
        return 0
    try:
        return _syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0),
                        ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except OSError:
        return 0


def _handled_access(abi: int) -> int:
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= REFER
    if abi >= 3:
        handled |= TRUNCATE
    if abi >= 5:
        handled |= IOCTL_DEV
    return handled


def session_env(home: Path, tmp: Path) -> dict[str, str]:
    """The environment of an R session: allowed variables only, private HOME and TMPDIR."""
    env = {
        name: value for name, value in os.environ.items()
        if (name in _ENV_NAMES or name.startswith(_ENV_PREFIXES)) and not _ENV_DENIED.search(name)
    }
    # Compose files written before the engine's Python moved to a venv set the system
    # Python, which has no mofapy2; MOFA2 would then build a conda environment instead.
    if env.get("RETICULATE_PYTHON") == "/usr/bin/python3" and os.path.exists(_VENV_PYTHON):
        env["RETICULATE_PYTHON"] = _VENV_PYTHON
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    return env


def allowed_paths(note: Path, env: dict[str, str]) -> list[tuple[str, int]]:
    """Paths the session may reach, with the access each gets (before masking by ABI)."""
    caches = [env[name] for name in _CACHE_VARS if env.get(name)]
    for cache in caches:
        os.makedirs(cache, exist_ok=True)
    r_paths = [p for name in _R_PATH_VARS for p in env.get(name, "").split(":") if p]
    shared = [(p, _READ) for p in (*_SYSTEM_PATHS, *r_paths)] + [(p, ~0) for p in caches]
    for path, _ in shared:
        if note.is_relative_to(os.path.realpath(path)):
            raise SandboxUnavailable(f"{path} contains the note folder {note}; allowing it would expose every note")
    return (
        [(str(note), ~0)] + shared
        + [(p, _DEVICE) for p in _DEVICES]
        # Read-only /proc: R, Quarto (dart-sass) and their child processes read their own
        # entries. Landlock refuses ptrace-mode access to processes outside the session, so
        # other processes' environment, memory and files stay hidden; only their command
        # lines and status are visible.
        + [("/proc", READ_FILE | READ_DIR)]
    )


def _add_rule(ruleset_fd: int, path: str, access: int) -> None:
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        rule = _PathBeneath(access, fd)
        _syscall(_SYS_ADD_RULE, ctypes.c_long(ruleset_fd), ctypes.c_long(_RULE_PATH_BENEATH),
                 ctypes.byref(rule), ctypes.c_uint32(0))
    finally:
        os.close(fd)


def _confine(folder: Path) -> None:
    """Restrict this process to folder and the shared paths. Runs in the launcher."""
    abi = landlock_abi()
    handled = _handled_access(abi)
    attr = _RulesetAttr(handled, 0, _SCOPE_SIGNAL | _SCOPE_ABSTRACT_UNIX_SOCKET if abi >= 6 else 0)
    ruleset = _syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)),
                       ctypes.c_uint32(0))
    for path, access in allowed_paths(folder, dict(os.environ)):
        try:
            _add_rule(ruleset, path, access & handled)
        except FileNotFoundError:
            pass  # e.g. /lib32 or /opt on a system without them
    if _libc.prctl(_PR_SET_NO_NEW_PRIVS, ctypes.c_ulong(1), ctypes.c_ulong(0),
                   ctypes.c_ulong(0), ctypes.c_ulong(0)):
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
    _syscall(_SYS_RESTRICT_SELF, ctypes.c_long(ruleset), ctypes.c_uint32(0))
    os.close(ruleset)


def confined_command(folder: str, command: list[str]) -> tuple[list[str], dict[str, str]]:
    """The argv and env that run command confined to folder (a note's, or a throwaway one),
    with a private HOME and TMPDIR inside it."""
    note = Path(folder).resolve()
    # Inside the session's own folder (note_kernel.KERNEL_DIR_REL), which a copy of the
    # note without its data leaves out.
    session = note / ".omicsbase" / "note-kernel"
    home, tmp = session / "home", session / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = session_env(home, tmp)

    abi = 0 if MODE == "off" else landlock_abi()
    if not abi and MODE == "required":
        raise SandboxUnavailable(
            "NOTE_SANDBOX=required, but this kernel offers no Landlock, so R was not started.")
    if not abi and MODE != "off":
        logger.warning("Landlock is not available: R sessions are not confined to their note folder")
    if abi:
        allowed_paths(note, env)  # fails here, with a clear message, on a misconfigured path
    launcher = [sys.executable, "-I", os.path.abspath(__file__), str(note), "landlock" if abi else "plain"]
    return launcher + command, env


def session_command(project_dir: str, command: list[str]) -> tuple[list[str], dict[str, str]]:
    """The argv and env that start the R session of the note in project_dir."""
    # A killed session leaves its temporary files behind. Only here: a Quarto render of
    # the note shares the folder and may run beside the session.
    shutil.rmtree(Path(project_dir).resolve() / ".omicsbase" / "note-kernel" / "tmp", ignore_errors=True)
    return confined_command(project_dir, command)


def main(args: list[str]) -> None:
    folder, confine, command = Path(args[0]), args[1] == "landlock", args[2:]
    with open("/proc/self/oom_score_adj", "w") as f:
        f.write("1000")
    if confine:
        _confine(folder)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])
