"""Tests for project-local artifact retrieval and durable memory."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.agent_runtime import (
    durable_project_memory,
    update_durable_project_memory,
)
from app.services import artifact_retrieval
from app.services.artifact_retrieval import INDEX_PATH, search_workspace


class _Db:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_search_ranks_semantically_related_result_and_refreshes_changes(tmp_path):
    project = tmp_path / "project"
    results = project / "output" / "results"
    code = project / "code" / "primary"
    results.mkdir(parents=True)
    code.mkdir(parents=True)
    (results / "alpha_diversity.csv").write_text(
        "sample_id,shannon,observed,group\nS1,2.1,10,Control\n"
    )
    (code / "permanova.qmd").write_text(
        "# Community comparison\nRun adonis2 with Bray distance.\n"
    )

    diversity = search_workspace(str(project), "Shannon richness by group")
    assert diversity["matches"][0]["path"] == "output/results/alpha_diversity.csv"
    assert (project / INDEX_PATH).exists()

    new_result = results / "limrots_differential_abundance.csv"
    new_result.write_text("feature,logFC,p.value\nTaxonA,2.4,0.01\n")
    differential = search_workspace(str(project), "LimROTS abundance changes")
    assert differential["index_refreshed"] is True
    assert differential["matches"][0]["path"] == (
        "output/results/limrots_differential_abundance.csv"
    )


def test_durable_memory_deduplicates_and_preserves_runtime_state():
    project = SimpleNamespace(
        agent_memory={"state": "completed", "summary": "Report ready"}
    )
    db = _Db()
    update = {
        "category": "preference",
        "content": "Use BH correction for primary analyses",
        "source": "user",
        "evidence": "Please use BH correction.",
    }

    update_durable_project_memory(db, project, [update])
    update_durable_project_memory(db, project, [update])

    memory = durable_project_memory(project)
    assert project.agent_memory["state"] == "completed"
    assert len(memory["preferences"]) == 1
    assert memory["preferences"][0]["content"] == (
        "Use BH correction for primary analyses"
    )
    assert db.commits == 2


def test_search_does_not_rehash_unchanged_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    code = project / "code"
    code.mkdir(parents=True)
    source = code / "analysis.qmd"
    source.write_text("# Shannon diversity\n")

    original_fingerprint = artifact_retrieval._fingerprint
    calls = 0

    def counting_fingerprint(path):
        nonlocal calls
        calls += 1
        return original_fingerprint(path)

    monkeypatch.setattr(artifact_retrieval, "_fingerprint", counting_fingerprint)

    first = search_workspace(str(project), "Shannon")
    assert first["index_refreshed"] is True
    assert calls == 1

    calls = 0
    second = search_workspace(str(project), "Shannon")
    assert second["index_refreshed"] is False
    assert calls == 0

    source.write_text("# Shannon diversity by treatment group\n")
    third = search_workspace(str(project), "treatment")
    assert third["index_refreshed"] is True
    assert calls == 1
