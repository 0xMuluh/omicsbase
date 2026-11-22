"""Pairing a machine of the user's own (deployment/roihu/connect.py)."""
import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

CONNECT = Path(__file__).resolve().parents[1] / "deployment" / "roihu" / "connect.py"


class Slept(Exception):
    """Raised in place of the first sleep, to end a wait that would not end."""


@pytest.fixture
def connect(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("connect", CONNECT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path / "attach.json")
    monkeypatch.setattr(module.signal, "signal", lambda *_: None)

    def sleep(_):
        raise Slept

    monkeypatch.setattr(module.time, "sleep", sleep)
    return module


def answer(connect, monkeypatch, status):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, status, "", {}, io.BytesIO())

    monkeypatch.setattr(connect.urllib.request, "urlopen", urlopen)


@pytest.mark.parametrize("status, text", [(404, "unknown, used or expired"), (409, "already have a Reports session")])
def test_a_code_that_cannot_work_is_reported_once_without_exiting(connect, monkeypatch, capsys, status, text):
    """Exiting would let `--restart unless-stopped` start the container into the same error."""
    answer(connect, monkeypatch, status)
    with pytest.raises(Slept):
        connect.settings("https://omicsbase.example", "ABCDE-FGHJK", "lab-pc")
    out = capsys.readouterr().out
    assert out.startswith("OmicsBase Reports: ") and text in out
    assert "docker rm -f omicsbase-reports" in out
    assert not connect.STATE.exists()


def test_an_error_that_may_pass_exits_so_the_container_tries_again(connect, monkeypatch):
    answer(connect, monkeypatch, 502)
    with pytest.raises(SystemExit) as stopped:
        connect.settings("https://omicsbase.example", "ABCDE-FGHJK", "lab-pc")
    assert "answered 502" in str(stopped.value)

    def unreachable(request, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(connect.urllib.request, "urlopen", unreachable)
    with pytest.raises(SystemExit) as stopped:
        connect.settings("https://omicsbase.example", "ABCDE-FGHJK", "lab-pc")
    assert "could not reach https://omicsbase.example" in str(stopped.value)
