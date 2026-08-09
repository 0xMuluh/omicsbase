from __future__ import annotations

import random

from app.services.edit_engine import EditEngineError, parse_apply_patch


def test_malformed_patch_fuzz_never_escapes_parser_error_boundary():
    rng = random.Random(20260809)
    alphabet = ["", "@@", "+ok", "-old", " context", "*** Update File: code/a.R", "*** Add File: code/b.R", "*** End of File", "garbage"]
    for _ in range(300):
        lines = ["*** Begin Patch"] + [rng.choice(alphabet) for _ in range(rng.randint(0, 10))] + ["*** End Patch"]
        patch = "\n".join(lines)
        try:
            operations = parse_apply_patch(patch)
        except EditEngineError:
            continue
        assert all(operation.kind in {"create", "delete", "patch_hunks"} for operation in operations)
