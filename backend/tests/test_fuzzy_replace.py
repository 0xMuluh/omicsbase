"""Unit tests for the fuzzy_replace module."""

import pytest
from app.services.fuzzy_replace import fuzzy_replace, find_similar_lines


def test_exact_match():
    whole = "def foo():\n    return 42\n"
    search = "def foo():\n    return 42\n"
    replace = "def foo():\n    return 100\n"

    ok, updated, diag = fuzzy_replace(whole, search, replace)
    assert ok is True
    assert updated == "def foo():\n    return 100\n"
    assert diag is None


def test_whitespace_flexible_match():
    whole = "    def bar():\n        x = 1\n        y = 2\n"
    # Search block missing 4 spaces of leading indentation
    search = "def bar():\n    x = 1\n    y = 2\n"
    replace = "def bar():\n    x = 10\n    y = 20\n"

    ok, updated, diag = fuzzy_replace(whole, search, replace)
    assert ok is True
    assert "x = 10" in updated
    assert "    def bar():" in updated  # Preserves relative indentation


def test_dotdotdots_elision():
    whole = "header\nline1\nline2\nline3\nfooter\n"
    search = "header\n...\nfooter\n"
    replace = "header\nnew_middle\nfooter\n"

    ok, updated, diag = fuzzy_replace(whole, search, replace)
    assert ok is True
    assert "new_middle" in updated


def test_failure_diagnostic():
    whole = "ggplot(df, aes(x=A, y=B)) +\n  geom_point()\n"
    search = "ggplot(df, aes(x=NONEXISTENT, y=B)) +\n  geom_point()\n"
    replace = "ggplot(df, aes(x=A, y=B)) +\n  geom_boxplot()\n"

    ok, updated, diag = fuzzy_replace(whole, search, replace)
    assert ok is False
    assert updated == whole
    assert diag is not None
    assert "Did you mean" in diag or "SEARCH block failed" in diag
