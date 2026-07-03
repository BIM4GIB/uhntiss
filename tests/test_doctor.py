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
    # The dev repo has a real .env; simulate a machine without one.
    monkeypatch.setattr(cli_module, "_api_key_from_env_file", lambda: None)
    with FakeAbleton([_SESSION, _DRUMS_ONE_KIT]) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port)])
    assert result.exit_code == 1
    assert "FAIL ANTHROPIC_API_KEY not set" in result.output


def test_doctor_accepts_key_from_env_file(monkeypatch):
    # The M4L glue reads .env directly; doctor must agree with the device
    # instead of failing in an unsourced shell.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "_api_key_from_env_file", lambda: "sk-from-file")
    with FakeAbleton([_SESSION, _DRUMS_ONE_KIT]) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port)])
    assert result.exit_code == 0, result.output
    assert "found in .env" in result.output


def test_doctor_bridge_probe_flags_missing_fork_commands(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    responses = [
        _SESSION,
        _DRUMS_ONE_KIT,
        {"status": "ok", "result": {"name": "clip", "is_audio": True}},  # get_selected_clip
        {"status": "error", "message": "Unknown command type: set_clip_envelope"},
    ]
    with FakeAbleton(responses) as fake:
        result = runner.invoke(cli_module.app, ["doctor", "--port", str(fake.port), "--bridge"])
    assert result.exit_code == 1
    assert "ok   bridge command get_selected_clip present" in result.output
    assert "bridge command set_clip_envelope missing" in result.output


def test_probe_bridge_classifies_replies_correctly():
    """Transport failure = FAIL (couldn't ask); 'unknown command' = missing;
    any genuine bridge reply (ok or param error) = present."""
    from mouthflow.execute import AbletonError, AbletonTransportError

    class StubClient:
        def __init__(self, outcomes):
            self.outcomes = outcomes  # cmd -> result | Exception

        def send_command(self, cmd, params=None):
            out = self.outcomes[cmd]
            if isinstance(out, Exception):
                raise out
            return out

    # A hung/refused socket must be a failure, never "present".
    failures: list[str] = []
    cli_module._probe_bridge(
        StubClient({
            "get_selected_clip": AbletonTransportError("get_selected_clip failed after reconnect"),
            "set_clip_envelope": AbletonTransportError("set_clip_envelope failed mid-flight"),
        }),
        failures,
    )
    assert len(failures) == 2
    assert all("probe" in f and "failed" in f for f in failures)

    # A param complaint proves the handler exists; unknown command = missing.
    failures = []
    cli_module._probe_bridge(
        StubClient({
            "get_selected_clip": {"name": "clip"},
            "set_clip_envelope": AbletonError("Invalid track_index -1"),
        }),
        failures,
    )
    assert failures == []


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
