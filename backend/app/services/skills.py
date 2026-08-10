"""Bounded, registry-facing just-in-time skill discovery and loading."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings

MAX_SKILL_LIST_LIMIT = 20
MAX_SKILL_LOAD_CHARS = 20_000
MAX_SKILL_REFERENCES = 8


class SkillError(ValueError):
    """Raised internally when a skill request is unsafe or unavailable."""


def _skills_root() -> Path:
    configured = str(getattr(settings, "skills_dir", "") or "").strip()
    root = Path(configured) if configured else Path(settings.prompts_dir).resolve().parent / "skills"
    return root.expanduser().resolve()


def _safe_relative(value: str, *, label: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise SkillError(f"{label} must be a safe relative path")
    return path.as_posix()


def _inside(root: Path, candidate: Path, *, label: str) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SkillError(f"{label} escaped the skills root") from exc
    return resolved


def _skill_directory(skill: str) -> tuple[Path, Path, str]:
    root = _skills_root()
    relative = _safe_relative(skill, label="skill")
    raw_directory = root / relative
    if raw_directory.is_symlink():
        raise SkillError(f"Skill may not be a symlink: {skill}")
    directory = _inside(root, raw_directory, label="skill")
    raw_skill_file = directory / "SKILL.md"
    if raw_skill_file.is_symlink():
        raise SkillError(f"SKILL.md may not be a symlink: {skill}")
    skill_file = _inside(root, raw_skill_file, label="SKILL.md")
    if not directory.is_dir() or not skill_file.is_file() or skill_file.is_symlink():
        raise SkillError(f"Unknown skill: {skill}")
    return root, directory, relative


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_bounded(path: Path, limit: int) -> tuple[str, bool]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return text[:limit], len(text) > limit


def _available_references(directory: Path) -> list[str]:
    references: list[str] = []
    references_root = directory / "references"
    if not references_root.is_dir():
        return references
    for path in sorted(references_root.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        references.append(path.relative_to(directory).as_posix())
    return references


def _skill_summary(path: Path) -> tuple[str, str]:
    text, _ = _read_bounded(path, 1200)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = path.parent.name
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("#").strip() or title
            break
    summary = next((line for line in lines if not line.startswith("#")), "")
    return title[:120], summary[:240]


def list_skills(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """List available skills without loading their full instructions."""
    arguments = arguments or {}
    try:
        limit = max(1, min(int(arguments.get("limit", MAX_SKILL_LIST_LIMIT)), MAX_SKILL_LIST_LIMIT))
        root = _skills_root()
        skills: list[dict[str, Any]] = []
        if root.is_dir():
            for skill_file in sorted(root.rglob("SKILL.md")):
                if skill_file.is_symlink() or not skill_file.is_file():
                    continue
                directory = skill_file.parent
                try:
                    skill_id = directory.relative_to(root).as_posix()
                except ValueError:
                    continue
                title, summary = _skill_summary(skill_file)
                skills.append(
                    {
                        "id": skill_id,
                        "name": title,
                        "summary": summary,
                        "sha256": _sha256(skill_file),
                        "available_references": _available_references(directory),
                    }
                )
                if len(skills) >= limit:
                    break
        return {"status": "ok", "skills": skills, "root_available": root.is_dir()}
    except (OSError, TypeError, ValueError) as exc:
        return {"status": "error", "error": str(exc)[:4000]}


def load_skill(
    skill: str,
    references: list[str] | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Load one skill and only explicitly named markdown references."""
    try:
        limit = max(512, min(int(max_chars or 12_000), MAX_SKILL_LOAD_CHARS))
        requested = list(references or [])
        if len(requested) > MAX_SKILL_REFERENCES:
            raise SkillError(f"At most {MAX_SKILL_REFERENCES} skill references may be loaded")
        if len(requested) != len(set(requested)):
            raise SkillError("Skill references must be unique")

        root, directory, skill_id = _skill_directory(skill)
        skill_file = directory / "SKILL.md"
        content, truncated = _read_bounded(skill_file, limit)
        available = _available_references(directory)
        loaded_references: list[dict[str, Any]] = []
        remaining = max(0, limit - len(content))
        for index, raw_reference in enumerate(requested):
            reference = _safe_relative(raw_reference, label="skill reference")
            if not reference.startswith("references/") or not reference.lower().endswith(".md"):
                raise SkillError("Skill references must be markdown files under references/")
            raw_candidate = directory / reference
            if raw_candidate.is_symlink():
                raise SkillError(f"Skill reference may not be a symlink: {raw_reference}")
            candidate = _inside(directory, raw_candidate, label="skill reference")
            if candidate.is_symlink() or not candidate.is_file():
                raise SkillError(f"Unknown skill reference: {raw_reference}")
            if reference not in available:
                raise SkillError(f"Unknown skill reference: {raw_reference}")
            if remaining <= 0:
                reference_content, reference_truncated = "", True
            else:
                slots_left = len(requested) - index
                budget = max(1, min(remaining, remaining // slots_left))
                reference_content, reference_truncated = _read_bounded(candidate, budget)
                remaining -= len(reference_content)
            loaded_references.append(
                {
                    "path": reference,
                    "content": reference_content,
                    "sha256": _sha256(candidate),
                    "truncated": reference_truncated,
                }
            )
        return {
            "status": "ok",
            "skill": skill_id,
            "skill_sha256": _sha256(skill_file),
            "content": content,
            "truncated": truncated,
            "references": loaded_references,
            "available_references": available,
            "root_relative": str(skill_file.relative_to(root).as_posix()),
        }
    except (OSError, TypeError, ValueError, SkillError) as exc:
        return {"status": "error", "error": str(exc)[:4000]}


__all__ = ["list_skills", "load_skill"]
