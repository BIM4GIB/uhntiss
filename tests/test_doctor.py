"""Tests for the ``mouthflow doctor`` preflight command.

Uses the same loopback ``FakeAbleton`` server as test_execute, driven through
the CLI with Typer's CliRunner. _log writes to stderr; click's CliRunner
merges stderr into ``result.output`` (mix_stderr defaults True on click 8.1),
so assertions read the check lines from there.
"""

from __future__ import annotations

import socket

from typer.testing import CliRunner

from mouthflow import cli as cli_module
from test_execute import FakeAbleton

runner = CliRunner()

_SESSION = {"status": "ok", "result": {"tempo": 120.0}}
_DRUMS_ONE_KIT = {
    "status": "ok",
    "result": {
        "path": "Drums",
        "items": [
            {
                "name": "808 Core Kit",
                "is_folder": False,
                "is_loadable": True,
                "uri": "query:Drums#FileId_5006",
            },
        ],
    },
}
_DRUMS_EMPTY = {"status": "ok", "result": {"path": "Drums", "items": []}}


def _free_port() -> int:
    """An almost-certainly-closed port: bind to get one, then release it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_doctor_passes_when_key_and_live_ok(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with FakeAbleton([_SESSION, _DRUMS_ONE_KIT]) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port)])
    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY is set" in result.output
    assert "1 drum kit(s) discovered" in result.output
    assert "all checks passed" in result.output


def test_doctor_fails_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with FakeAbleton([_SESSION, _DRUMS_ONE_KIT]) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port)])
    assert result.exit_code == 1
    assert "FAIL ANTHROPIC_API_KEY not set" in result.output


def test_doctor_fails_cleanly_when_unreachable(monkeypatch):
    # No server listening -> ConnectionRefusedError must be caught (clean exit
    # 1, not a traceback). Guards the OSError handling.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(cli_module.app, ["doctor", "--port", str(_free_port())])
    assert result.exit_code == 1
    assert "not reachable" in result.output
    # A handled exit, not an uncaught exception leaking a traceback.
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_doctor_fails_when_no_kits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with FakeAbleton([_SESSION, _DRUMS_EMPTY]) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port)])
    assert result.exit_code == 1
    assert "no loadable drum kits" in result.output
