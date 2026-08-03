"""Global test configuration — enable dev_mode so auth fallbacks work in tests."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _enable_dev_mode(monkeypatch):
    """All tests run in dev_mode to allow default tenant/user header fallbacks."""
    monkeypatch.setattr(settings, "dev_mode", True)
