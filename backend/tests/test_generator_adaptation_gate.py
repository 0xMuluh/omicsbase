"""Regression tests for truthful exemplar adaptation outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.schemas import AnalysisPlan
from app.services import generator, qa_gate, spawner
from app.services.report_pack import ReportPack, ReportPackFile, ReportPackRule


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        project_name="Adaptation Gate",
        domain="microbiome",
        study_type="exploratory",
        question="Describe this study",
        grouping_variable=None,
        group_levels=[],
        workflow=[],
    )


def test_material_adaptation_gate_ignores_formatting_and_r_comments():
    original = "# Original note\nvalue <- 1\n"

    assert not generator._is_material_adaptation(
        original,
        "# Reworded note\n  value   <-   1\n",
        "code/analysis.R",
    )
    assert generator._is_material_adaptation(
        original,
        "# Original note\nvalue <- 2\n",
        "code/analysis.R",
    )
    assert generator._is_material_adaptation(
        "## Original result\n",
        "## Study-specific result\n",
        "code/report.qmd",
    )


def test_material_adaptation_gate_ignores_inline_r_and_yaml_comments():
    assert not generator._is_material_adaptation(
        "value <- 1 # template explanation\n",
        "value <- 1 # current-study explanation\n",
        "code/analysis.R",
    )
    assert not generator._is_material_adaptation(
        "theme: cosmo # template theme\n",
        "theme: cosmo # current-study theme\n",
        "code/_quarto.yml",
    )
    assert generator._is_material_adaptation(
        'label <- "group#1" # explanation\n',
        'label <- "group#2" # explanation\n',
        "code/analysis.R",
    )


def test_qa_removal_partition_preserves_none_and_blocks_required():
    classifications = {
        "code/static.qmd": ReportPackFile(
            path="code/static.qmd",
            role="page",
            adaptation="none",
        ),
        "code/required.qmd": ReportPackFile(
            path="code/required.qmd",
            role="page",
            adaptation="required",
        ),
    }

    prunable, preserved, blocked = generator._partition_qa_removals(
        ["static.qmd", "required.qmd", "generated.qmd"],
        classifications,
    )

    assert prunable == ["generated.qmd"]
    assert preserved == ["static.qmd"]
    assert blocked == ["required.qmd"]


def test_final_generated_source_closure_rejects_missing_source(tmp_path: Path):
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "main.R").write_text('source("missing-helper.R")\n')
    report_pack = SimpleNamespace(
        execution=SimpleNamespace(working_directory="code"),
    )

    with pytest.raises(
        generator.GenerationQualityError,
        match="Generated source dependency closure is invalid.*missing-helper.R",
    ):
        generator._validate_adapted_source_closure(tmp_path, report_pack)


def _qa_policy_pack(root: Path) -> ReportPack:
    root.mkdir()
    return ReportPack(
        root=root,
        pack_id="qa-policy-pack",
        version="1.0.0",
        domain="microbiome",
        name="QA policy pack",
        default_adaptation="inspect",
        rules=(
            ReportPackRule("loader", "code/data.R", "data_loader", "none"),
            ReportPackRule("helper", "code/funct.R", "helper", "none"),
            ReportPackRule("orchestrator", "code/main.R", "orchestrator", "none"),
            ReportPackRule("assembly", "code/_quarto.yml", "assembly", "none"),
            ReportPackRule("static-page", "code/static.qmd", "page", "none"),
            ReportPackRule("required-page", "code/required.qmd", "page", "required"),
        ),
    )


@pytest.mark.parametrize("finding_kind", ["structural", "language"])
@pytest.mark.asyncio
async def test_qa_cannot_delete_none_or_required_pack_files(
    tmp_path: Path,
    monkeypatch,
    finding_kind: str,
):
    project_dir = tmp_path / "project"
    pack = _qa_policy_pack(tmp_path / "pack")
    required_original = (
        "---\ntitle: Required\n---\n\nTemplate cohort marker.\n"
        + "Generic scientific content.\n" * 20
    )
    files = {
        "code/data.R": "value <- 1\n",
        "code/funct.R": "summarize_value <- function(x) summary(x)\n",
        "code/main.R": "quarto::quarto_render()\n",
        "code/_quarto.yml": "project:\n  type: website\n  render: []\n",
        "code/static.qmd": "---\ntitle: Static\n---\n\nStatic pack content.\n",
        "code/required.qmd": required_original,
    }

    def fake_spawn(
        project_dir: str,
        pack: ReportPack,
        **_kwargs,
    ) -> dict[str, str]:
        base = Path(project_dir)
        for relative, content in files.items():
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            if checkpoint := _kwargs.get("checkpoint"):
                checkpoint.complete(f"test_spawn:{relative}", [relative])
        return dict(files)

    async def fake_edits(
        instruction: str,
        file_content: str,
        *,
        target_file: str,
        **kwargs,
    ):
        if "Active ReportPack policy" in instruction:
            return generator.AdaptationEdits(
                edits=(
                    {
                        "search": "Template cohort marker.",
                        "replace": "Current cohort marker.",
                    },
                ),
                inspected_chunks=1,
                content_sha256=generator._content_hash(file_content),
            )
        return generator.DeleteDecision(
            inspected_chunks=1,
            content_sha256=generator._content_hash(file_content),
        )

    def fake_qa(*args, **kwargs):
        result = qa_gate.QaResult()
        getattr(result, finding_kind).extend(
            [
                "static.qmd" if finding_kind == "structural" else "static.qmd: finding",
                "required.qmd" if finding_kind == "structural" else "required.qmd: finding",
            ]
        )
        return result

    monkeypatch.setattr(spawner, "resolve_report_pack", lambda *args, **kwargs: pack)
    monkeypatch.setattr(spawner, "spawn_report_pack", fake_spawn)
    monkeypatch.setattr(generator, "load_system_prompt", lambda *args, **kwargs: "system")
    monkeypatch.setattr(generator, "_request_file_edits", fake_edits)
    monkeypatch.setattr(generator, "_write_project_scaffold", lambda *args, **kwargs: [])
    monkeypatch.setattr(qa_gate, "run_qa", fake_qa)
    monkeypatch.setattr(
        qa_gate,
        "prune_files",
        lambda *args, **kwargs: pytest.fail("protected QA files reached prune_files"),
    )
    monkeypatch.setattr(generator.settings, "qa_repair_rounds", 1)

    with pytest.raises(generator.GenerationQualityError, match="required ReportPack source"):
        await generator.generate_project(
            str(project_dir),
            _plan(),
            file_summaries=[],
            uploaded_file_paths={},
        )

    assert (project_dir / "code" / "static.qmd").is_file()
    assert (project_dir / "code" / "required.qmd").is_file()
    manifest = json.loads((project_dir / "adaptation_manifest.json").read_text())
    by_path = {item["path"]: item for item in manifest["files"]}
    assert by_path["code/static.qmd"]["status"] == "copied"
    assert by_path["code/required.qmd"]["status"] == "qa_removal_blocked"


@pytest.mark.asyncio
async def test_invalid_edit_response_is_not_treated_as_no_change(monkeypatch):
    async def invalid_response(**kwargs):
        return "I could not produce the requested edits."

    monkeypatch.setattr(generator, "call_llm", invalid_response)

    with pytest.raises(generator.AdaptationResponseError, match="not valid JSON"):
        await generator._request_file_edits(
            "Return edits.",
            "original",
            system_prompt="system",
            plan_json="{}",
            file_descriptions="none",
            uploaded_file_paths={},
            target_file="code/data.R",
            generated_context={},
        )


@pytest.mark.asyncio
async def test_empty_edit_array_requires_explicit_no_change(monkeypatch):
    async def empty_response(**kwargs):
        return "[]"

    monkeypatch.setattr(generator, "call_llm", empty_response)

    with pytest.raises(generator.AdaptationResponseError, match="explicit no_change"):
        await generator._request_file_edits(
            "Return edits.",
            "original",
            system_prompt="system",
            plan_json="{}",
            file_descriptions="none",
            uploaded_file_paths={},
            target_file="code/page.qmd",
            generated_context={},
        )


@pytest.mark.asyncio
async def test_reasoned_no_change_is_a_first_class_decision(monkeypatch):
    async def no_change_response(**kwargs):
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "The file contains only generic plotting helpers and no study fields.",
                "evidence": [
                    "The inspected source defines plotting helpers but no data paths or study columns."
                ],
            }
        )

    monkeypatch.setattr(generator, "call_llm", no_change_response)

    decision = await generator._request_file_edits(
        "Return edits.",
        "original",
        system_prompt="system",
        plan_json="{}",
        file_descriptions="none",
        uploaded_file_paths={},
        target_file="code/funct.R",
        generated_context={},
    )

    assert isinstance(decision, generator.NoChangeDecision)
    assert decision.inspected_chunks == 1
    assert decision.content_sha256 == generator._content_hash("original")
    assert "generic plotting helpers" in decision.reason
    assert decision.evidence == (
        "chunk 1: The inspected source defines plotting helpers but no data paths or study columns.",
    )


@pytest.mark.asyncio
async def test_no_change_without_inspection_evidence_is_rejected(monkeypatch):
    async def unsupported_no_change(**kwargs):
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "The file contains only generic plotting helpers and no study fields.",
            }
        )

    monkeypatch.setattr(generator, "call_llm", unsupported_no_change)

    with pytest.raises(generator.AdaptationResponseError, match="invalid decision object"):
        await generator._request_file_edits(
            "Return edits.",
            "original",
            system_prompt="system",
            plan_json="{}",
            file_descriptions="none",
            uploaded_file_paths={},
            target_file="code/funct.R",
            generated_context={},
        )


@pytest.mark.asyncio
async def test_adaptation_prompt_contains_manifest_and_safe_project_bindings(monkeypatch):
    captured: dict[str, str] = {}

    async def capture_prompt(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "The configured study column and project path already match.",
                "evidence": [
                    "The source reads ../data/counts.csv, which is the bound feature-table path."
                ],
            }
        )

    monkeypatch.setattr(generator, "call_llm", capture_prompt)
    await generator._request_file_edits(
        "Return edits.",
        "study_data <- read.csv('../data/counts.csv')",
        system_prompt="system",
        plan_json="{}",
        file_descriptions="counts.csv has columns sample_id and condition",
        uploaded_file_paths={
            "feature_table": ["/private/uploads/tenant-123/counts.csv"],
        },
        target_file="code/data.R",
        generated_context={},
        study_manifest_json=json.dumps(
            {
                "files": [
                    {
                        "name": "counts.csv",
                        "role": "feature_table",
                        "columns": ["sample_id", "condition"],
                    }
                ]
            }
        ),
    )

    prompt = captured["user_prompt"]
    assert "Validated Study Manifest" in prompt
    assert '"columns": ["sample_id", "condition"]' in prompt
    assert "feature_table[1]: ../data/counts.csv" in prompt
    assert "/private/uploads" not in prompt


@pytest.mark.asyncio
async def test_large_file_is_inspected_beyond_old_40k_cutoff(monkeypatch):
    marker = "prenatal diet marker after the old cutoff"
    content = ("generic line\n" * 3_600) + marker + "\n"
    calls = 0

    async def inspect_chunk(**kwargs):
        nonlocal calls
        calls += 1
        prompt = kwargs["user_prompt"]
        if marker in prompt:
            return json.dumps(
                [{"search": marker, "replace": "current study marker"}]
            )
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "This chunk contains generic source with no study-specific fields.",
                "evidence": [
                    "The inspected chunk contains no cohort labels, input paths, or study columns."
                ],
            }
        )

    monkeypatch.setattr(generator, "call_llm", inspect_chunk)
    decision = await generator._request_file_edits(
        "Inspect the complete file.",
        content,
        system_prompt="system",
        plan_json="{}",
        file_descriptions="none",
        uploaded_file_paths={},
        target_file="code/design/large.qmd",
        generated_context={},
    )

    assert isinstance(decision, generator.AdaptationEdits)
    assert calls == decision.inspected_chunks
    assert decision.inspected_chunks >= 2
    assert any(edit["search"] == marker for edit in decision.edits)


@pytest.mark.asyncio
async def test_one_malformed_edit_item_rejects_entire_response(monkeypatch):
    async def mixed_response(**kwargs):
        return json.dumps(
            [
                {"search": "original", "replace": "adapted"},
                {"search": "missing replace field"},
            ]
        )

    monkeypatch.setattr(generator, "call_llm", mixed_response)

    with pytest.raises(generator.AdaptationResponseError, match="malformed edit item 2"):
        await generator._request_file_edits(
            "Return edits.",
            "original",
            system_prompt="system",
            plan_json="{}",
            file_descriptions="none",
            uploaded_file_paths={},
            target_file="code/data.R",
            generated_context={},
        )


def test_edit_report_distinguishes_applied_and_rejected_edits():
    original = "\n".join(f"line {index}" for index in range(20))
    updated, applied, rejected = generator._apply_edits_with_report(
        original,
        [
            {"search": "line 3", "replace": "line three"},
            {"search": "missing line", "replace": "replacement"},
        ],
        "code/data.R",
    )

    # A mixed payload is one adaptation unit: the failed second edit aborts
    # the valid first edit so a checkpoint can never claim a partial file.
    assert updated == original
    assert applied == 0
    assert len(rejected) == 1


@pytest.mark.asyncio
async def test_provider_failure_writes_manifest_and_blocks_study_files(
    tmp_path: Path,
    monkeypatch,
):
    def minimal_spawn(project_dir: str, pack, **_kwargs) -> dict[str, str]:
        base = Path(project_dir)
        files = {
            "code/data.R": "study_data <- data.frame(group = c('A', 'B'))\n",
            "code/funct.R": "summarize_study <- function(x) summary(x)\n",
            "code/main.R": "quarto::quarto_render()\n",
            "code/_quarto.yml": "project:\n  type: website\n  render: []\n",
            "README.md": "# Generic pack documentation\n",
        }
        for relative, content in files.items():
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            if checkpoint := _kwargs.get("checkpoint"):
                checkpoint.complete(f"test_spawn:{relative}", [relative])
        return files

    called_targets: list[str] = []

    async def unavailable_provider(**kwargs):
        user_prompt = kwargs.get("user_prompt", "")
        target_marker = "The current file `"
        if target_marker in user_prompt:
            called_targets.append(user_prompt.split(target_marker, 1)[1].split("`", 1)[0])
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(spawner, "spawn_report_pack", minimal_spawn)
    monkeypatch.setattr(generator, "load_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(generator, "call_llm", unavailable_provider)

    with pytest.raises(generator.AdaptationGateError, match="report-pack source"):
        await generator.generate_project(
            str(tmp_path),
            _plan(),
            file_summaries=[],
            uploaded_file_paths={},
        )

    manifest = json.loads((tmp_path / "adaptation_manifest.json").read_text())
    assert manifest["summary"]["failed"] >= 1
    by_path = {item["path"]: item for item in manifest["files"]}
    assert by_path["code/data.R"]["status"] == "failed"
    assert by_path["code/data.R"]["reason"] == "provider unavailable"
    assert by_path["code/main.R"]["status"] == "failed"
    assert by_path["README.md"]["status"] == "copied"
    assert by_path["README.md"]["adaptation"] == "none"
    assert "README.md" not in called_targets
    assert manifest["report_pack"]["manifest_sha256"]
    assert (tmp_path / "code" / "data.R").read_text().startswith("study_data")


@pytest.mark.asyncio
async def test_required_file_cannot_use_no_change_decision(
    tmp_path: Path,
    monkeypatch,
):
    def minimal_spawn(project_dir: str, pack, **_kwargs) -> dict[str, str]:
        base = Path(project_dir)
        files = {
            "code/data.R": "study_data <- read.csv('../data/template.csv')\n",
            "code/funct.R": "summarize_study <- function(x) summary(x)\n",
            "code/main.R": "quarto::quarto_render()\n",
            "code/_quarto.yml": "project:\n  type: website\n  render: []\n",
        }
        for relative, content in files.items():
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            if checkpoint := _kwargs.get("checkpoint"):
                checkpoint.complete(f"test_spawn:{relative}", [relative])
        return files

    async def no_change(**kwargs):
        return json.dumps(
            {
                "decision": "no_change",
                "reason": "The template already looks internally consistent.",
                "evidence": [
                    "The inspected file contains a syntactically valid template data binding."
                ],
            }
        )

    monkeypatch.setattr(spawner, "spawn_report_pack", minimal_spawn)
    monkeypatch.setattr(generator, "load_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(generator, "call_llm", no_change)

    with pytest.raises(generator.AdaptationGateError, match="code/data.R"):
        await generator.generate_project(
            str(tmp_path),
            _plan(),
            file_summaries=[],
            uploaded_file_paths={},
        )

    manifest = json.loads((tmp_path / "adaptation_manifest.json").read_text())
    by_path = {item["path"]: item for item in manifest["files"]}
    assert by_path["code/data.R"]["status"] == "failed"
    assert "requires a material study adaptation" in by_path["code/data.R"]["reason"]
    assert by_path["code/funct.R"]["status"] == "inspected_no_change"
    assert by_path["code/main.R"]["status"] == "inspected_no_change"


def test_adaptation_manifest_records_deliberate_unchanged_state(tmp_path: Path):
    target = generator._write_adaptation_manifest(
        tmp_path,
        [
            {
                "path": "code/data.R",
                "kind": "script",
                "status": "inspected_no_change",
                "reason": "The configured path already matches the current study.",
                "source_sha256": "abc",
                "result_sha256": "abc",
            }
        ],
    )

    payload = json.loads(target.read_text())
    assert payload["summary"] == {"inspected_no_change": 1}
    assert payload["files"][0]["source_sha256"] == payload["files"][0]["result_sha256"]


def test_manifest_finalization_rehashes_qa_mutations(tmp_path: Path):
    target = tmp_path / "code" / "page.qmd"
    target.parent.mkdir(parents=True)
    target.write_text("after QA repair")
    outcomes = [
        {
            "path": "code/page.qmd",
            "status": "adapted",
            "source_sha256": "source",
            "result_sha256": "before-qa",
        }
    ]

    generator._finalize_adaptation_outcomes(tmp_path, outcomes)

    assert outcomes[0]["adaptation_status"] == "adapted"
    assert outcomes[0]["status"] == "qa_repaired"
    assert outcomes[0]["result_sha256"] == generator._content_hash("after QA repair")
    assert outcomes[0]["finalized"] is True
