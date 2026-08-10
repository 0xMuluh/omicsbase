from app.services.generator import (
    _dependency_inputs,
    _dependency_paths_for_step,
)


def test_qmd_dependency_context_is_small_and_content_addressed():
    files = {
        "code/data.R": "data",
        "code/funct.R": "helpers",
        "code/index.qmd": "index",
        "code/primary/old.qmd": "old",
        "README.md": "readme",
    }
    paths = _dependency_paths_for_step("qmd_pages", files)
    assert paths == {"code/data.R", "code/funct.R", "code/index.qmd"}
    first = _dependency_inputs(files, paths)
    files["code/data.R"] = "changed"
    second = _dependency_inputs(files, paths)
    assert first["code/funct.R"] == second["code/funct.R"]
    assert first["code/data.R"] != second["code/data.R"]
