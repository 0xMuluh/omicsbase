"""Regression test: NoteThread cells share one persistent R workspace."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest

from app.config import settings


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not available")
def test_cells_share_one_persistent_workspace(monkeypatch, tmp_path):
    from app.services import note_execution

    monkeypatch.setattr(settings, "use_docker_sandbox", False)
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "note_execution_shared_workspace", True)

    async def run(source: str) -> tuple[str, dict, str | None]:
        return await note_execution.execute_r_cell(
            project_dir=str(tmp_path),
            execution_id=str(uuid.uuid4()),
            source=source,
            language="r",
            parameters={},
            timeout_seconds=60,
        )

    async def scenario():
        status, metadata, error = await run("answer <- 6 * 7")
        assert status == "completed", error
        status, metadata, error = await run("cat(answer)")
        assert status == "completed", error
        assert "42" in metadata["stdout_preview"]

        status, metadata, error = await run("library(tools)")
        assert status == "completed", error
        status, metadata, error = await run('cat("tools" %in% .packages(), answer + 1)')
        assert status == "completed", error
        assert "TRUE 43" in metadata["stdout_preview"]
        assert "Loading required package" not in metadata["stdout_preview"]

        status, metadata, error = await run('library(tools)\nwarning("visible warning")')
        assert status == "completed", error
        assert "Loading required package" not in metadata["stdout_preview"]
        assert "visible warning" in metadata["stdout_preview"]

        status, metadata, error = await run("broken <- 1\nstop('boom')")
        assert status == "completed_with_errors", f"expected completed_with_errors, got {status}"
        assert "Error: boom" in metadata["stdout_preview"]
        assert any(item["type"] == "error" for item in metadata["events"])
        status, metadata, error = await run("cat(broken)")
        assert status == "completed", error
        assert "1" in metadata["stdout_preview"]

        status, metadata, error = await run('write.csv(data.frame(sample = c("a", "b"), value = c(1.5, 2.5)), "out_table.csv", row.names = FALSE)')
        assert status == "completed", error
        artifact_types = [a["artifact_type"] for a in metadata["artifacts"]]
        assert "table" in artifact_types, artifact_types
        table = next(a for a in metadata["artifacts"] if a["artifact_type"] == "table")
        assert table["relative_path"] == "out_table.csv"
        assert (tmp_path / "out_table.csv").is_file()

        # Structured capture: bare expressions auto-print, visible data frames
        # become table artifacts, explicit print(df) is intercepted, plots replay.
        status, metadata, error = await run("x <- 6 * 7\nx")
        assert status == "completed", error
        assert "42" in metadata["stdout_preview"]

        status, metadata, error = await run("print(data.frame(taxon = c('a', 'b'), reads = c(10L, 20L)))")
        assert status == "completed", error
        assert "[table: 2 rows x 2 cols]" in metadata["stdout_preview"]
        table_types = [a["artifact_type"] for a in metadata["artifacts"]]
        assert "table" in table_types, table_types

        status, metadata, error = await run("df <- data.frame(g = c('a', 'b', 'c'), v = c(1, 2, 3))\ndf")
        assert status == "completed", error
        assert "[table: 3 rows x 2 cols]" in metadata["stdout_preview"]

        status, metadata, error = await run("plot(1:5, main = 'structured plot')")
        assert status == "completed", error
        assert any(a["artifact_type"] == "image" for a in metadata["artifacts"]), metadata["artifacts"]
        assert any(item["type"] == "plot" for item in metadata["events"])

    asyncio.run(scenario())

    assert (tmp_path / ".omicsbase" / "note-kernel" / "workspace.RData").is_file()
    objects_path = tmp_path / ".omicsbase" / "note-kernel" / "workspace-objects.txt"
    assert objects_path.is_file()
    names = objects_path.read_text(encoding="utf-8").splitlines()
    assert "answer" in names
    assert "broken" in names


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not available")
def test_note_agent_context_reports_workspace_objects(monkeypatch, tmp_path):
    from app.api import projects_notes
    from app.models.notes import NoteThread
    from app.config import settings

    monkeypatch.setattr(settings, "projects_dir", str(tmp_path))

    thread = NoteThread(id=str(uuid.uuid4()), title="Objects note", thread_type="note")
    thread.cells = []
    objects_dir = tmp_path / "notes" / str(thread.id) / ".omicsbase" / "note-kernel"
    objects_dir.mkdir(parents=True, exist_ok=True)
    (objects_dir / "workspace-objects.txt").write_text("ps\nmetadata_df\n", encoding="utf-8")

    context = projects_notes._note_agent_context(db=None, thread=thread)
    assert context["workspace_objects"] == ["ps", "metadata_df"]
