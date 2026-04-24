"""Tests for mouthflow.transcribe.

Real accuracy targets (onset F1 >= 0.75, drum class acc >= 0.65) gate on
the 20-clip corpus and live in eval/run_eval.py. These tests assert
pipeline shape, API contract, and that the classifier distinguishes
obvious synthetic stimuli.
"""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from mouthflow import transcribe
from mouthflow.transcribe import (
    DROP,
    GM_HAT_CLOSED,
    GM_KICK,
    GM_SNARE,
    _classify,
    _features_at,
    _quantise_16th,
    transcribe_drums,
)

SR = 44_100


def _kick_sample(duration_s: float = 0.12) -> np.ndarray:
    """Low sine with fast decay."""
    t = np.arange(int(SR * duration_s)) / SR
    env = np.exp(-t * 40)
    return (0.8 * env * np.sin(2 * np.pi * 60 * t)).astype(np.float32)


def _snare_sample(duration_s: float = 0.12) -> np.ndarray:
    """Low-passed noise + tone around 200 Hz (centroid ~2 kHz)."""
    t = np.arange(int(SR * duration_s)) / SR
    env = np.exp(-t * 25)
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(len(t)).astype(np.float32)
    # Heavy low-pass pushes centroid toward the 1.5-3 kHz range of a real
    # snare; without it, white noise centroid sits > 10 kHz.
    k = 32
    kernel = np.ones(k, dtype=np.float32) / k
    noise = np.convolve(noise, kernel, mode="same") * 0.5
    tone_low = 0.5 * np.sin(2 * np.pi * 200 * t)
    tone_mid = 0.2 * np.sin(2 * np.pi * 1800 * t)
    return (env * (noise + tone_low + tone_mid)).astype(np.float32)


def _hat_sample(duration_s: float = 0.05) -> np.ndarray:
    """High-passed short noise burst."""
    t = np.arange(int(SR * duration_s)) / SR
    env = np.exp(-t * 80)
    rng = np.random.default_rng(1)
    x = rng.standard_normal(len(t)).astype(np.float32)
    # Cheap HP via differentiation.
    x = np.diff(x, prepend=0)
    return (0.6 * env * x).astype(np.float32)


def _place(events: list[tuple[float, np.ndarray]], total_s: float) -> np.ndarray:
    out = np.zeros(int(total_s * SR), dtype=np.float32)
    for t, sample in events:
        start = int(t * SR)
        end = min(start + len(sample), len(out))
        out[start:end] += sample[: end - start]
    return out


def test_classify_kick_snare_hat_drop():
    y = _kick_sample()
    assert _classify(_features_at(y, SR, 0.0)) == GM_KICK

    y = _snare_sample()
    assert _classify(_features_at(y, SR, 0.0)) == GM_SNARE

    y = _hat_sample()
    assert _classify(_features_at(y, SR, 0.0)) == GM_HAT_CLOSED

    silence = np.zeros(int(SR * 0.12), dtype=np.float32)
    assert _classify(_features_at(silence, SR, 0.0)) == DROP


def test_quantise_16th_snaps_to_grid():
    step = 60 / 120 / 4  # 125 ms at 120 BPM
    assert _quantise_16th(0.123, 120) == pytest.approx(step)
    assert _quantise_16th(0.0, 120) == 0.0


def test_transcribe_drums_end_to_end(tmp_path):
    # 2s of a steady 4-on-the-floor kick at 120 BPM, beat every 500 ms.
    y = _place([(t, _kick_sample()) for t in (0.0, 0.5, 1.0, 1.5)], total_s=2.0)
    wav = tmp_path / "kick.wav"
    sf.write(wav, y, SR, subtype="PCM_16")

    result = transcribe_drums(wav)

    assert result.midi_path.exists()
    assert result.tempo_bpm > 0
    assert result.bars > 0
    assert len(result.hits) >= 3
    # All kicks should be recognised as kicks.
    kicks = [h for h in result.hits if h.midi_note == GM_KICK]
    assert len(kicks) >= 3

    # MIDI file is loadable and has notes on channel 9 (GM drums).
    mid = mido.MidiFile(result.midi_path)
    note_ons = [
        m for track in mid.tracks for m in track if m.type == "note_on" and m.velocity > 0
    ]
    assert note_ons, "expected at least one note_on"
    assert all(m.channel == 9 for m in note_ons)
