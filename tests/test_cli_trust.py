"""Tests for the CLI's trust contract: never lose a take, never damage the
session, never burn an LLM call on silence.

Uses the loopback ``FakeAbleton`` server (from test_execute) through Typer's
CliRunner, with the mic mocked.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from mouthflow import cli as cli_module
from mouthflow.schemas import ClipPlan, Plan
from test_execute import FakeAbleton

runner = CliRunner()
SR = 44_100

_SESSION_100 = {"status": "ok", "result": {"tempo": 100.0}}
_TRACK_0 = {"status": "ok", "result": {"index": 0}}


def _four_on_the_floor_wav(path: Path, seconds: float = 2.0) -> Path:
    y = np.zeros(int(seconds * SR), dtype=np.float32)
    kick_dur = int(0.12 * SR)
    t = np.arange(kick_dur) / SR
    kick = (0.8 * np.exp(-t * 40) * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
    for n in range(int(seconds / 0.5)):
        start = int(n * 0.5 * SR)
        y[start : start + kick_dur] += kick
    sf.write(path, y, SR, subtype="PCM_16")
    return path


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _fake_make_plan(captured: dict):
    def fake(transcription, session_state, user_hint=None, **_kwargs):
        captured["transcription"] = transcription
        instr = session_state["available_instruments"][0]
        uri = instr["uri"] if isinstance(instr, dict) else instr
        return Plan(
            tempo=transcription.tempo_bpm,
            clips=[ClipPlan(track_name="Drums", instrument_path=uri,
                            midi_file=transcription.midi_path,
                            length_bars=max(transcription.bars, 1.0))],
            rationale="mocked",
        )

    return fake


def test_silent_take_exits_without_llm_or_junk_track(tmp_path, monkeypatch):
    """A silent take must not reach the planner (no LLM burn) and must not
    create an empty clip on a junk track."""
    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(SR, dtype=np.float32), SR, subtype="PCM_16")

    def boom(*_a, **_k):
        raise AssertionError("make_plan must not run on a silent take")

    monkeypatch.setattr(cli_module, "make_plan", boom)
    result = runner.invoke(cli_module.app, ["dry-run", str(silent), "--instruments", "u1"])
    assert result.exit_code == 3
    assert "heard nothing usable" in result.output


def test_record_fails_fast_before_opening_the_mic(monkeypatch):
    """Ableton down must cost zero performances: the socket is checked BEFORE
    recording starts."""

    def boom(*_a, **_k):
        raise AssertionError("the mic must not open when Ableton is unreachable")

    monkeypatch.setattr(cli_module.capture, "record", boom)
    result = runner.invoke(cli_module.app, ["record", "--port", str(_free_port())])
    assert result.exit_code == 1
    assert "not reachable" in result.output


def test_record_uses_project_tempo_and_leaves_it_alone(tmp_path, monkeypatch):
    """One clock: the take is transcribed against the project tempo, and the
    project tempo is never overwritten by default."""
    wav = _four_on_the_floor_wav(tmp_path / "take.wav")
    monkeypatch.setattr(cli_module.capture, "record", lambda *a, **k: wav)
    monkeypatch.setattr(cli_module, "_STATE_DIR", tmp_path / ".mouthflow")
    monkeypatch.setattr(cli_module, "_LAST_TAKE", tmp_path / ".mouthflow" / "last_take.json")
    captured: dict = {}
    monkeypatch.setattr(cli_module, "make_plan", _fake_make_plan(captured))

    with FakeAbleton([_SESSION_100, _TRACK_0]) as fake:
        result = runner.invoke(
            cli_module.app,
            ["record", "--duration", "1", "--port", str(fake.port),
             "--instruments", "query:Drums#KitA"],
        )
    assert result.exit_code == 0, result.output
    assert "using project tempo 100.0" in result.output
    # transcription ran against the project's clock, not a librosa guess
    assert captured["transcription"].tempo_bpm == 100.0
    # the session's tempo was read, never written
    types = [r["type"] for r in fake.requests]
    assert types[0] == "get_session_info"
    assert "set_tempo" not in types
    # the take + flags were remembered for retry-last
    state = json.loads((tmp_path / ".mouthflow" / "last_take.json").read_text())
    assert state["wav"] == str(wav)
    assert state["device"] == "drums"


def test_retry_last_replays_saved_take(tmp_path, monkeypatch):
    wav = _four_on_the_floor_wav(tmp_path / "take.wav")
    last = tmp_path / "last_take.json"
    last.write_text(json.dumps({
        "wav": str(wav), "device": "drums", "instruments": "query:Drums#KitA",
        "set_tempo": False, "bars": "auto", "bar_align": True, "correct": True,
    }))
    monkeypatch.setattr(cli_module, "_LAST_TAKE", last)
    captured: dict = {}
    monkeypatch.setattr(cli_module, "make_plan", _fake_make_plan(captured))

    with FakeAbleton([_TRACK_0]) as fake:
        result = runner.invoke(cli_module.app, ["retry-last", "--port", str(fake.port)])
    assert result.exit_code == 0, result.output
    assert "retrying take" in result.output
    assert captured["transcription"].hits, "the saved take was transcribed"
    types = [r["type"] for r in fake.requests]
    assert "create_midi_track" in types and "fire_clip" in types
    assert "set_tempo" not in types


def test_retry_last_without_saved_take_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_LAST_TAKE", tmp_path / "missing.json")
    result = runner.invoke(cli_module.app, ["retry-last", "--port", str(_free_port())])
    assert result.exit_code == 1
    assert "no saved take" in result.output
