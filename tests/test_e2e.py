"""End-to-end pipeline test via the CLI's dry-run subcommand.

Mocks anthropic (via plan.make_plan's client= parameter is indirect — we
monkeypatch the module function) and uses a synthetic WAV that exercises
capture + classify + transcribe for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from mouthflow import cli as cli_module
from mouthflow.schemas import ClipPlan, Plan

SR = 44_100


def _four_on_the_floor_wav(path: Path, seconds: float = 2.0) -> None:
    y = np.zeros(int(seconds * SR), dtype=np.float32)
    kick_dur = int(0.12 * SR)
    t = np.arange(kick_dur) / SR
    env = np.exp(-t * 40)
    kick = (0.8 * env * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
    beat_s = 0.5  # 120 BPM
    for n in range(int(seconds / beat_s)):
        start = int(n * beat_s * SR)
        y[start : start + kick_dur] += kick
    sf.write(path, y, SR, subtype="PCM_16")


def test_cli_dry_run_end_to_end(tmp_path, monkeypatch):
    wav = tmp_path / "loop.wav"
    _four_on_the_floor_wav(wav)

    captured_args: dict = {}

    def fake_make_plan(transcription, session_state, user_hint=None, **_kwargs):
        captured_args["transcription"] = transcription
        captured_args["session_state"] = session_state
        captured_args["hint"] = user_hint
        return Plan(
            tempo=transcription.tempo_bpm,
            clips=[
                ClipPlan(
                    track_name="Drums",
                    instrument_path=session_state["available_instruments"][0],
                    midi_file=transcription.midi_path,
                    length_bars=transcription.bars,
                )
            ],
            rationale="mocked",
        )

    monkeypatch.setattr(cli_module, "make_plan", fake_make_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "dry-run",
            str(wav),
            "--instruments",
            "query:Drums#Kit-A,query:Drums#Kit-B",
            "--hint",
            "harder",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "{" in out, f"no JSON in output:\n{out!r}"
    start = out.index("{")
    plan = json.loads(out[start:])
    assert plan["rationale"] == "mocked"
    assert plan["clips"][0]["instrument_path"] == "query:Drums#Kit-A"

    assert captured_args["hint"] == "harder"
    assert captured_args["session_state"]["available_instruments"] == [
        "query:Drums#Kit-A",
        "query:Drums#Kit-B",
    ]
    assert captured_args["transcription"].tempo_bpm > 0
    assert len(captured_args["transcription"].hits) >= 3
