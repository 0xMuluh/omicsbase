from app.services import generator
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


def test_report_pack_dependency_hashes_only_declared_inputs():
    common = {
        "plan_json": '{"grouping_variable":"condition","question":"original"}',
        "file_descriptions": "counts.csv",
        "uploaded_file_paths": {},
        "study_manifest_json": "{}",
        "generated_files": {"code/funct.R": "helpers"},
    }

    original = generator._adaptation_dependency_inputs(
        ("grouping_variable",), **common
    )
    question_changed = generator._adaptation_dependency_inputs(
        ("grouping_variable",),
        **{**common, "plan_json": '{"grouping_variable":"condition","question":"changed"}'},
    )
    grouping_changed = generator._adaptation_dependency_inputs(
        ("grouping_variable",),
        **{**common, "plan_json": '{"grouping_variable":"arm","question":"original"}'},
    )

    assert original == question_changed
    assert original != grouping_changed
    assert generator._adaptation_dependency_inputs((), **common) == {}


def test_declared_dependencies_drive_generated_context_paths():
    files = {
        "code/data.R": "data",
        "code/other.qmd": "page",
        "code/third.qmd": "page",
        "output/results.csv": "results",
        "code/target.R": "target",
    }

    assert generator._adaptation_context_paths(
        "page", "code/target.R", files, ("report_pages",)
    ) == {"code/other.qmd", "code/third.qmd"}
    assert generator._adaptation_context_paths(
        "script", "code/target.R", files, ("result_artifacts",)
    ) == {"output/results.csv"}
    assert generator._adaptation_context_paths(
        "script", "code/target.R", files, ()
    ) == set()


def test_legacy_context_is_inferred_from_target_references():
    files = {
        "code/target.R": "source(\"helper.R\")\n",
        "code/helper.R": "helper <- function(x) x\n",
        "code/unrelated.R": "unrelated <- TRUE\n",
    }

    assert generator._adaptation_context_paths(
        "script", "code/target.R", files, None
    ) == {"code/helper.R"}
