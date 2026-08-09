"""Tests for the fast inline-edit streaming endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models.project import Project
from app.services.edit_engine import sha256_bytes

client = TestClient(app)


@pytest.fixture
def inline_project(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_local()
    project = Project(
        name="Inline edit test",
        question="test",
        status="completed",
        tenant_id="default_tenant",
        owner_id="default_user",
        project_dir=str(tmp_path),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "index.qmd").write_text("library(ggplot2)\nggplot(df, aes(x=a)) + theme_bw()\n")
    (code_dir / "01_alpha_diversity.qmd").write_text(
        "library(phyloseq)\nplot_richness(ps, x='sample_side') + scale_color_manual(values = c('blue', 'red'))\n"
    )

    def override_get_db():
        scoped = session_local()
        try:
            yield scoped
        finally:
            scoped.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield project
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        Base.metadata.drop_all(bind=engine)



def test_inline_edit_endpoint_structure(inline_project):
    content = "library(ggplot2)\nggplot(df, aes(x=a)) + theme_bw()\n"
    project_dir = Path(inline_project.project_dir)
    payload = {
        "project_id": str(inline_project.id),
        "path": "code/index.qmd",
        "prompt": "Change theme to theme_minimal()",
        "selection": "theme_bw()",
        "content": content,
        "base_sha256": sha256_bytes((project_dir / "code/index.qmd").read_bytes()),
    }
    response = client.post("/api/inline-edit", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = response.text.strip().split("\n")
    assert len(lines) >= 2
    assert "start" in lines[0]


def test_inline_edit_rich_context(inline_project):
    content = "library(phyloseq)\nplot_richness(ps, x='sample_side') + scale_color_manual(values = c('blue', 'red'))\n"
    project_dir = Path(inline_project.project_dir)
    payload = {
        "project_id": str(inline_project.id),
        "path": "code/01_alpha_diversity.qmd",
        "prompt": "Fix color palette for sample_side variable",
        "selection": "scale_color_manual(values = c('blue', 'red'))",
        "content": content,
        "base_sha256": sha256_bytes((project_dir / "code/01_alpha_diversity.qmd").read_bytes()),
        "project_context": "Project: Gut Microbiome Study\nDataset: phyloseq object with sample_side (Left, Right)",
        "error_context": "Error in scale_color_manual: Insufficient values in manual scale. 3 needed but 2 provided.",
    }
    response = client.post("/api/inline-edit", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = response.text.strip().split("\n")
    assert len(lines) >= 2
    assert "start" in lines[0]


def test_inline_edit_requires_project_scope_and_base_hash():
    response = client.post(
        "/api/inline-edit",
        json={"path": "code/index.qmd", "prompt": "change", "content": ""},
    )
    assert response.status_code == 422
