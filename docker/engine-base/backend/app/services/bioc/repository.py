"""Repository resolution and materialisation for Bioconductor QMD sources."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from app.services.bioc_qmd import _sha256, iter_qmd_files


def tree_fingerprint(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(_sha256(path.read_bytes()).encode("ascii"))
    return digest.hexdigest()


def git_command(command: list[str], *, timeout: int = 600) -> str:
    """Run a bounded read-only or repository-maintenance Git command."""
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()[-2000:]
        raise RuntimeError("git command failed: " + " ".join(command) + ": " + detail)
    return (completed.stdout or "").strip()


def remote_ref_names(repository_url: str, kind: str, *, git_command_fn: Callable[..., str] | None = None) -> list[str]:
    command_runner = git_command_fn or git_command
    output = command_runner(
        ["git", "ls-remote", f"--{kind}", "--refs", repository_url],
        timeout=120,
    )
    prefix = f"refs/{kind}/"
    names = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2 or not parts[1].startswith(prefix):
            continue
        names.append(parts[1][len(prefix):])
    return names


def default_remote_branch(repository_url: str, *, git_command_fn: Callable[..., str] | None = None) -> str | None:
    command_runner = git_command_fn or git_command
    output = command_runner(
        ["git", "ls-remote", "--symref", repository_url, "HEAD"],
        timeout=120,
    )
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/"):].split("\t", 1)[0]
    return None


def version_key(value: str) -> tuple[int, ...] | None:
    cleaned = value.lstrip("vV")
    pieces = cleaned.split(".")
    if len(pieces) < 2 or not all(piece.isdigit() for piece in pieces[:2]):
        return None
    return tuple(int(piece) for piece in pieces[:4])


def resolve_repository_reference(
    repository_url: str,
    requested: str,
    channel: str,
    *,
    remote_ref_names_fn: Callable[[str, str], list[str]] | None = None,
    default_remote_branch_fn: Callable[[str], str | None] | None = None,
) -> str:
    """Resolve moving catalog aliases to a concrete stable or preview ref."""
    requested = str(requested or "").strip()
    if requested and requested.lower() not in {
        "default", "auto", "latest", "latest_release", "latest_release_branch", "latest_tag",
    }:
        return requested

    refs = remote_ref_names_fn or remote_ref_names
    default_branch = default_remote_branch_fn or default_remote_branch
    if requested.lower() in {"latest_tag"}:
        tags = refs(repository_url, "tags")
        candidates = [(key, tag) for tag in tags if (key := version_key(tag)) is not None]
        if candidates:
            return max(candidates)[1]
        raise RuntimeError("No versioned release tags found for " + repository_url)

    branches = refs(repository_url, "heads")
    if channel == "preview":
        for preferred in ("devel", "sandbox", "main", "master"):
            if preferred in branches:
                return preferred
    else:
        release_candidates = []
        for branch in branches:
            normalized = branch.replace("-", "_").replace(".", "_")
            parts = normalized.split("_")
            if len(parts) == 3 and parts[0].upper() == "RELEASE" and all(part.isdigit() for part in parts[1:]):
                release_candidates.append((int(parts[1]), int(parts[2]), branch))
        if release_candidates:
            return max(release_candidates)[2]
        for preferred in ("release", "stable", "main", "master"):
            if preferred in branches:
                return preferred

    default = default_branch(repository_url)
    if default:
        return default
    raise RuntimeError("Could not resolve a " + channel + " ref for " + repository_url)


def materialise_repository(
    entry: dict,
    channel: str,
    storage_root: Path,
    *,
    resolve_reference_fn: Callable[[str, str, str], str] | None = None,
    git_command_fn: Callable[..., str] | None = None,
    tree_fingerprint_fn: Callable[[Path, list[Path]], str] | None = None,
) -> tuple[Path, str, str | None, str]:
    """Fetch a stable/preview QMD source into a persistent shallow mirror."""
    local_path = entry.get("source_path") or entry.get("repository_path")
    requested = str(
        entry.get("stable_ref" if channel == "stable" else "preview_ref")
        or ("auto" if channel == "stable" else "devel")
    ).strip()
    fingerprint = tree_fingerprint_fn or tree_fingerprint
    command_runner = git_command_fn or git_command
    if local_path:
        root = Path(str(local_path)).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Configured QMD source does not exist: {root}")
        paths = list(iter_qmd_files(root))
        return root, fingerprint(root, paths), None, requested

    repository_url = str(entry.get("repository_url") or "").strip()
    if not repository_url:
        raise ValueError("Catalog entry needs source_path or repository_url")
    if not repository_url.startswith(("https://github.com/", "https://git.bioconductor.org/")) or any(char.isspace() for char in repository_url):
        raise ValueError("Repository URL must use an approved Bioconductor/GitHub HTTPS host")
    resolver = resolve_reference_fn or resolve_repository_reference
    resolved = resolver(repository_url, requested, channel)
    slug = str(entry.get("slug") or "book").strip()
    safe_slug = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in slug)
    target = storage_root / "repositories" / safe_slug / channel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    # Mirrors created at build time belong to a different uid; git refuses to
    # operate on them unless the directory is marked safe for this process.
    command_runner(["git", "config", "--global", "--add", "safe.directory", str(target)], timeout=30)

    if (target / ".git").is_dir():
        command_runner(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", resolved],
            timeout=600,
        )
        command_runner(["git", "-C", str(target), "checkout", "--force", "FETCH_HEAD"], timeout=120)
        command_runner(["git", "-C", str(target), "clean", "-fdx"], timeout=120)
    else:
        if target.exists():
            shutil.rmtree(target)
        command_runner(
            ["git", "clone", "--depth", "1", "--branch", resolved, repository_url, str(target)],
            timeout=600,
        )

    commit_sha = command_runner(["git", "-C", str(target), "rev-parse", "HEAD"], timeout=30)
    paths = list(iter_qmd_files(target))
    return target, commit_sha or fingerprint(target, paths), commit_sha, resolved


__all__ = [
    "default_remote_branch",
    "git_command",
    "materialise_repository",
    "remote_ref_names",
    "resolve_repository_reference",
    "tree_fingerprint",
    "version_key",
]
