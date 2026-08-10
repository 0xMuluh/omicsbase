from types import SimpleNamespace

from app.services.tool_specs import TOOL_REGISTRY
from app.services.workspace_agent import (
    _inspect_factor_levels,
    _read_results,
    _read_workspace_file,
    _summarize_missingness,
)


def _project(tmp_path):
    return SimpleNamespace(project_dir=str(tmp_path))


def test_read_file_supports_line_windows_and_revision_metadata(tmp_path):
    path = tmp_path / "code" / "script.R"
    path.parent.mkdir()
    path.write_text("one\ntwo\nthree\nfour\n")

    result = _read_workspace_file(
        _project(tmp_path),
        "code/script.R",
        start_line=2,
        end_line=3,
    )
    assert result["content"] == "two\nthree\n"
    assert result["line_start"] == 2
    assert result["line_end"] == 3
    assert len(result["sha256"]) == 64
    assert result["revision"] == result["sha256"]


def test_read_results_filters_sorts_and_pages_rows(tmp_path):
    path = tmp_path / "output" / "results" / "answer.csv"
    path.parent.mkdir(parents=True)
    path.write_text("group,value\nA,2\nB,4\nA,6\n")

    result = _read_results(
        _project(tmp_path),
        "output/results/answer.csv",
        {"where": {"group": "A"}, "sort": "value", "sort_direction": "desc", "limit": 1},
    )
    assert result["rows"] == [{"group": "A", "value": "6"}]
    assert result["row_count"] == 2
    assert result["returned_rows"] == 1


def test_typed_table_tools_return_counts_without_raw_rows(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("group,value\nA,2\nA,\nB,4\n")

    levels = _inspect_factor_levels(_project(tmp_path), {"path": "data.csv", "column": "group"})
    missing = _summarize_missingness(_project(tmp_path), {"path": "data.csv"})
    schema = _read_workspace_file(_project(tmp_path), "data.csv")
    assert levels["level_count"] == 2
    assert levels["levels"][0] == {"level": "A", "count": 2}
    assert missing["missing"]["value"]["count"] == 1
    assert schema["schema"]["column_info"]["group"]["levels"] is None


def test_read_tool_contracts_are_explicit():
    read_file = TOOL_REGISTRY.require("read_file", lens="workspace").parameters
    assert read_file["properties"]["around"]["type"] == "integer"
    read_results = TOOL_REGISTRY.require("read_results", lens="workspace").parameters
    assert read_results["properties"]["limit"]["maximum"] == 200
    assert TOOL_REGISTRY.require("ask_user", lens="workspace").parameters["required"] == ["question", "options"]
    assert {name for name in ("inspect_table", "inspect_factor_levels", "summarize_missingness") if TOOL_REGISTRY.get(name, lens="workspace")} == {
        "inspect_table",
        "inspect_factor_levels",
        "summarize_missingness",
    }
