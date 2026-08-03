"""Tests for the fast inline-edit streaming endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_inline_edit_endpoint_structure():
    payload = {
        "path": "code/index.qmd",
        "prompt": "Change theme to theme_minimal()",
        "selection": "theme_bw()",
        "content": "library(ggplot2)\nggplot(df, aes(x=a)) + theme_bw()\n",
    }
    response = client.post("/api/inline-edit", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = response.text.strip().split("\n")
    assert len(lines) >= 2
    assert "start" in lines[0]


def test_inline_edit_rich_context():
    payload = {
        "path": "code/01_alpha_diversity.qmd",
        "prompt": "Fix color palette for sample_side variable",
        "selection": "scale_color_manual(values = c('blue', 'red'))",
        "content": "library(phyloseq)\nplot_richness(ps, x='sample_side') + scale_color_manual(values = c('blue', 'red'))\n",
        "project_context": "Project: Gut Microbiome Study\nDataset: phyloseq object with sample_side (Left, Right)",
        "error_context": "Error in scale_color_manual: Insufficient values in manual scale. 3 needed but 2 provided.",
    }
    response = client.post("/api/inline-edit", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = response.text.strip().split("\n")
    assert len(lines) >= 2
    assert "start" in lines[0]
