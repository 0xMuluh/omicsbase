"""Structured apply-patch parsing and hunk application."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from app.services.edit_engine import EditMatchError, EditOperation, EditPatchError

@dataclass(frozen=True)
class _PatchSpec:
    kind: Literal["create", "delete", "update"]
    path: str
    lines: tuple[str, ...] = ()
    hunks: tuple[tuple[str, ...], ...] = ()
    eof: bool = False
    no_final_newline: bool = False


def parse_apply_patch(patch: str) -> list[EditOperation]:
    """Parse the Codex-style ``*** Begin Patch`` envelope into operations."""

    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise EditPatchError("Patch must start with *** Begin Patch.")
    if "*** End Patch" not in lines:
        raise EditPatchError("Patch must end with *** End Patch.")
    end = lines.index("*** End Patch")
    specs: list[_PatchSpec] = []
    i = 1
    while i < end:
        line = lines[i]
        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ").strip()
            i += 1
            added: list[str] = []
            while i < end and not lines[i].startswith("*** "):
                if not lines[i].startswith("+"):
                    raise EditPatchError("Added-file patch lines must begin with +.", path=path)
                added.append(lines[i][1:])
                i += 1
            specs.append(_PatchSpec("create", path, lines=tuple(added)))
            continue
        if line.startswith("*** Delete File: "):
            specs.append(_PatchSpec("delete", line.removeprefix("*** Delete File: ").strip()))
            i += 1
            continue
        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ").strip()
            i += 1
            hunks: list[tuple[str, ...]] = []
            current: list[str] = []
            eof = False
            no_final_newline = False
            while i < end and not lines[i].startswith("*** "):
                if lines[i].startswith("@@"):
                    if current:
                        hunks.append(tuple(current))
                        current = []
                    i += 1
                    continue
                if lines[i] == "\\ No newline at end of file":
                    no_final_newline = True
                    i += 1
                    continue
                if not lines[i] or lines[i][0] not in {" ", "+", "-"}:
                    raise EditPatchError("Malformed patch hunk line.", path=path)
                current.append(lines[i])
                i += 1
            if current:
                hunks.append(tuple(current))
            if i < end and lines[i] == "*** End of File":
                eof = True
                i += 1
            if not hunks:
                raise EditPatchError("Update patch has no hunks.", path=path)
            specs.append(
                _PatchSpec(
                    "update",
                    path,
                    hunks=tuple(hunks),
                    eof=eof,
                    no_final_newline=no_final_newline,
                )
            )
            continue
        raise EditPatchError(f"Unexpected patch line: {line}")

    operations: list[EditOperation] = []
    for spec in specs:
        if spec.kind == "create":
            operations.append(EditOperation(path=spec.path, kind="create", content="\n".join(spec.lines) + ("\n" if spec.lines else "")))
        elif spec.kind == "delete":
            operations.append(EditOperation(path=spec.path, kind="delete"))
        else:
            operations.append(EditOperation(path=spec.path, kind="patch_hunks", patch=_encode_update_patch(spec)))
    return operations


def _encode_update_patch(spec: _PatchSpec) -> str:
    return json.dumps(
        {
            "path": spec.path,
            "hunks": [list(hunk) for hunk in spec.hunks],
            "eof": spec.eof,
            "no_final_newline": spec.no_final_newline,
        }
    )


def _decode_hunks(value: str) -> tuple[tuple[tuple[str, ...], ...], bool, bool]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EditPatchError("Encoded patch hunks are invalid.") from exc
    hunks = payload.get("hunks") if isinstance(payload, dict) else None
    if not isinstance(hunks, list) or not hunks or any(not isinstance(hunk, list) or any(not isinstance(line, str) for line in hunk) for hunk in hunks):
        raise EditPatchError("Encoded patch hunks are invalid.")
    return (
        tuple(tuple(hunk) for hunk in hunks),
        bool(payload.get("eof", False)),
        bool(payload.get("no_final_newline", False)),
    )


def _apply_hunks(
    whole: bytes,
    hunks: tuple[tuple[str, ...], ...],
    *,
    path: str,
    require_eof: bool = False,
    no_final_newline: bool = False,
) -> bytes:
    try:
        text = whole.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditPatchError("Patch target is not valid UTF-8.", path=path) from exc
    had_final_newline = text.endswith("\n")
    lines = text.splitlines()
    for hunk in hunks:
        old_lines = [line[1:] for line in hunk if line and line[0] in {" ", "-"}]
        new_lines = [line[1:] for line in hunk if line and line[0] in {" ", "+"}]
        if not old_lines:
            lines.extend(new_lines)
            continue
        candidates = [index for index in range(len(lines) - len(old_lines) + 1) if lines[index : index + len(old_lines)] == old_lines]
        if len(candidates) != 1:
            raise EditMatchError("Patch hunk context did not match exactly one location.", path=path, details={"matches": len(candidates)})
        start = candidates[0]
        if require_eof and start + len(old_lines) != len(lines):
            raise EditMatchError("Patch hunk marked End of File but its context is not at EOF.", path=path)
        lines[start : start + len(old_lines)] = new_lines
    updated = "\n".join(lines)
    if had_final_newline:
        updated += "\n"
    if no_final_newline:
        updated = updated.rstrip("\n")
    return updated.encode("utf-8")


