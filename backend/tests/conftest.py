"""Global test configuration — enable dev_mode so auth fallbacks work in tests."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _enable_dev_mode(monkeypatch):
    """Keep tests in development mode without changing the selected runtime."""
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "project_agent_enabled", True)
