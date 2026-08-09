from __future__ import annotations

import yaml

from app.services.editor import (
    _apply_edits,
    _build_edit_prompt,
    _collect_source_files,
    _editor_contract_context,
)


def _materialized_pack(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "main.R").write_text("source('validate.R')\n")
    (code / "analysis.R").write_text("x <- 1\n")
    (code / "validate.R").write_text("stopifnot(TRUE)\n")
    (code / "page.qmd").write_text("---\ntitle: Report\n---\n")
    (tmp_path / "report_pack.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "id": "test-pack",
                "version": "1.0.0",
                "domain": "microbiome",
                "name": "Test pack",
                "entrypoint": "code/main.R",
                "default_adaptation": "inspect",
                "execution": {
                    "working_directory": "code",
                    "render": "entrypoint",
                    "steps": [
                        {"id": "validate", "path": "code/validate.R", "role": "validator"},
                    ],
                    "artifacts": ["output/index.html"],
                },
                "capabilities": {
                    "test-capability": {
                        "sources": ["code/analysis.R"],
                        "execution_steps": ["validate"],
                        "parameters": {},
                        "outputs": ["output/index.html"],
                        "validators": ["code/validate.R"],
                    },
                },
                "file_rules": [
                    {"id": "main", "match": "code/main.R", "role": "orchestrator", "adaptation": "inspect"},
                    {"id": "analysis", "match": "code/analysis.R", "role": "analysis", "adaptation": "required"},
                    {"id": "validator", "match": "code/validate.R", "role": "validator", "adaptation": "required"},
                    {"id": "pages", "match": "code/**/*.qmd", "role": "page", "adaptation": "inspect"},
                ],
            },
            sort_keys=False,
        )
    )
    return tmp_path


def test_editor_context_contains_pack_roles_hashes_and_protected_paths(tmp_path):
    project = _materialized_pack(tmp_path)

    contract, protected = _editor_contract_context(project)
    files = _collect_source_files(
        project,
        file_roles=contract["file_roles"],
        protected_paths=protected,
    )
    prompt = _build_edit_prompt(
        files,
        "Add a result caption",
        project_context={
            "analysis_plan": {"question": "Compare groups"},
            "study_manifest": {"status": "ready"},
        },
        report_pack_context=contract["metadata"],
        protected_paths=protected,
    )

    analysis = next(item for item in files if item["path"] == "code/analysis.R")
    assert analysis["role"] == "analysis"
    assert analysis["sha256"]
    assert "Compare groups" in prompt
    assert "test-pack" in prompt
    assert "code/validate.R" in prompt
    assert "role: analysis" in prompt
    assert "sha256:" in prompt


def test_editor_rejects_reportpack_validator_edits(tmp_path):
    project = _materialized_pack(tmp_path)
    _, protected = _editor_contract_context(project)
    target = project / "code" / "validate.R"
    before = target.read_text()

    result = _apply_edits(
        project,
        [{"path": "code/validate.R", "content": "stopifnot(FALSE)\n"}],
        protected_paths=protected,
    )

    assert not any(item.ok for item in result)
    assert target.read_text() == before
    assert result
