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

from mouthflow.devices.drum import classify as drum_classify
from mouthflow.transcribe import (
    DROP,
    GM_HAT_CLOSED,
    GM_KICK,
    GM_SNARE,
    _classify_heuristic,
    _detect_tempo,
    _detect_onsets,
    _features_at,
    _grid_phase,
    _quantise_16th,
    _quantise_grid,
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
    # The deterministic heuristic is the always-present baseline; the per-user
    # trained model is data-dependent and graded by eval/run_eval instead.
    y = _kick_sample()
    assert _classify_heuristic(_features_at(y, SR, 0.0)) == GM_KICK

    y = _snare_sample()
    assert _classify_heuristic(_features_at(y, SR, 0.0)) == GM_SNARE

    y = _hat_sample()
    assert _classify_heuristic(_features_at(y, SR, 0.0)) == GM_HAT_CLOSED

    silence = np.zeros(int(SR * 0.12), dtype=np.float32)
    assert _classify_heuristic(_features_at(silence, SR, 0.0)) == DROP


def test_quantise_16th_snaps_to_grid():
    step = 60 / 120 / 4  # 125 ms at 120 BPM
    assert _quantise_16th(0.123, 120) == pytest.approx(step)
    assert _quantise_16th(0.0, 120) == 0.0


def test_quantise_grid_phase():
    step = 60 / 120 / 4
    # phase=0 reduces to the plain 16th quantiser.
    assert _quantise_grid(0.123, 120, 0.0) == pytest.approx(_quantise_16th(0.123, 120))
    # A half-step phase offsets every grid line by step/2.
    assert _quantise_grid(0.0, 120, 0.5) == pytest.approx(0.5 * step)
    # _grid_phase recovers a known phase: onsets laid on the +0.25-step grid.
    onsets = np.array([(n + 0.25) * step for n in range(8)])
    assert _grid_phase(onsets, 120) == pytest.approx(0.25, abs=0.02)


def test_quantise_grid_never_negative():
    # A negative phase + a first-cell onset used to snap to a negative time,
    # which mido cannot serialize (the whole take crashed).
    assert _quantise_grid(0.01, 120, -0.4) == 0.0
    assert _quantise_grid(0.0, 120, -0.5) >= 0.0


def _drum_pattern(bpm: float, bars: int = 6) -> np.ndarray:
    """A boombap-ish kick/snare/hat loop at ``bpm`` (8th-note hats)."""
    beat = 60.0 / bpm
    events: list[tuple[float, np.ndarray]] = []
    for b in range(bars):
        base = b * 4 * beat
        events += [(base + s * beat, _kick_sample()) for s in (0, 2)]
        events += [(base + s * beat, _snare_sample()) for s in (1, 3)]
        events += [(base + s * beat, _hat_sample()) for s in (0.5, 1.5, 2.5, 3.5)]
    return _place(events, bars * 4 * beat + 0.3)


@pytest.mark.parametrize("bpm", [84, 100, 120])
def test_detect_tempo_no_octave_error(tmp_path, monkeypatch, bpm):
    # beat_track reports ~2x on beatbox; the estimator must land on the right
    # octave and refine to within the +-3 BPM eval tolerance.
    monkeypatch.setattr(drum_classify, "_MODEL", None)
    y = _drum_pattern(bpm)
    onsets = _detect_onsets(y, SR)
    coarse, conf = _detect_tempo(y, SR, onsets)
    assert conf > 0.0
    assert coarse < bpm * 1.5, f"octave error: {coarse} for true {bpm}"

    wav = tmp_path / "pattern.wav"
    sf.write(wav, y, SR, subtype="PCM_16")
    assert transcribe_drums(wav).tempo_bpm == pytest.approx(bpm, abs=3.0)


def test_explicit_tempo_overrides_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(drum_classify, "_MODEL", None)
    # Explicit tempo is honored verbatim, regardless of the audio's real tempo.
    y = _drum_pattern(100)
    wav = tmp_path / "pattern.wav"
    sf.write(wav, y, SR, subtype="PCM_16")
    assert transcribe_drums(wav, tempo=128.0).tempo_bpm == 128.0


def test_transcribe_drums_end_to_end(tmp_path, monkeypatch):
    # Force the deterministic heuristic: the per-user model is trained on real
    # beatbox, so synthetic tones aren't guaranteed to classify "correctly".
    # _classify reads _MODEL from its own module at call time, so patch there.
    monkeypatch.setattr(drum_classify, "_MODEL", None)
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
