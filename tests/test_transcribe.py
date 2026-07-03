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


def test_quantise_grid_strength_blends_toward_raw():
    step = 60 / 120 / 4
    t = 0.30  # 40 ms early of the 3rd 16th line (0.375... no: line 2 at 0.25; nearest is 0.25? )
    t = 0.28  # 30 ms late of the line at 0.25
    hard = _quantise_grid(t, 120, 0.0, strength=1.0)
    soft = _quantise_grid(t, 120, 0.0, strength=0.5)
    raw = _quantise_grid(t, 120, 0.0, strength=0.0)
    assert hard == pytest.approx(0.25)
    assert raw == pytest.approx(t)
    assert soft == pytest.approx((t + 0.25) / 2)  # halfway between raw and snapped
    assert abs(soft - 0.25) < abs(raw - 0.25)


def test_quantise_grid_swing_shifts_offbeat_lines():
    from mouthflow.devices.drum.tempo import _swing_frac

    step = 60 / 120 / 4
    # A shuffled performance: on-beats on the grid, off-beats 25% of a step late.
    onsets = []
    for n in range(8):
        onsets.append(n * 2 * step)
        onsets.append((n * 2 + 1) * step + 0.25 * step)
    swing = _swing_frac(np.array(onsets), 120, phase=0.0)
    assert swing == pytest.approx(0.25, abs=0.03)
    # An off-beat hit snaps to the SWUNG line, not the straight one.
    late_off = 1 * step + 0.25 * step + 0.005
    snapped = _quantise_grid(late_off, 120, 0.0, swing=swing)
    assert snapped == pytest.approx((1 + swing) * step, abs=0.004)
    # On-beat lines are untouched by swing.
    assert _quantise_grid(2 * step + 0.001, 120, 0.0, swing=swing) == pytest.approx(2 * step, abs=1e-6)


def test_swing_frac_gates_out_noise_and_sparse_data():
    from mouthflow.devices.drum.tempo import _swing_frac

    step = 60 / 120 / 4
    # Straight 16ths with tiny jitter -> no swing invented.
    rng = np.random.default_rng(7)
    straight = np.array([n * step + rng.normal(0, 0.002) for n in range(16)])
    assert _swing_frac(straight, 120, 0.0) == 0.0
    # Too few off-beat samples -> no swing.
    quarters = np.array([n * 4 * step for n in range(8)])
    assert _swing_frac(quarters, 120, 0.0) == 0.0


def test_bar_align_translates_instead_of_shearing(tmp_path, monkeypatch):
    """bar_align lands the performer's grid on Live's downbeat by TRANSLATING
    the clip — relative timing is preserved, not sheared hit-by-hit."""
    from mouthflow.devices.drum.transcriber import DrumTranscriber

    monkeypatch.setattr(drum_classify, "_MODEL", None)
    bpm = 100.0
    lead_in = 0.03  # performer starts 30 ms after t=0
    y = np.concatenate([np.zeros(int(lead_in * SR), dtype=np.float32), _drum_pattern(bpm)])
    wav = tmp_path / "offset.wav"
    sf.write(wav, y, SR, subtype="PCM_16")

    feel = DrumTranscriber().transcribe(wav, tempo=bpm, bar_align=False)
    grid = DrumTranscriber().transcribe(wav, tempo=bpm, bar_align=True)
    assert len(feel.hits) == len(grid.hits)
    # Same hits, shifted by one (near-)constant amount, not sheared per-hit.
    # (The first hit may clamp at 0, so allow a couple of ms of spread.)
    deltas = [f.time_s - g.time_s for f, g in zip(feel.hits, grid.hits)]
    assert max(deltas) - min(deltas) < 0.003
    assert deltas[-1] == pytest.approx(lead_in, abs=0.012)
    # And the aligned clip starts on the downbeat.
    assert grid.hits[0].time_s == pytest.approx(0.0, abs=0.012)


def test_velocities_from_rms_normalises_per_take():
    from mouthflow import signal

    # A flat take stays flat — no fake dynamics amplified out of noise.
    assert signal.velocities_from_rms([0.1] * 8 ) == [90] * 8

    # A dynamic take: quiet hits land as ghosts, loud ones as accents,
    # ordering follows loudness.
    rms = [0.02, 0.02, 0.3, 0.3, 0.02, 0.3, 0.05, 0.1]
    vels = signal.velocities_from_rms(rms)
    assert max(vels) >= 110 and min(vels) <= 60
    for a, b in [(0, 2), (6, 7), (7, 3)]:  # quieter index -> lower velocity
        assert vels[a] < vels[b]

    # Short takes fall back to the absolute map.
    assert signal.velocities_from_rms([0.1, 0.2]) == [
        signal.velocity_from_rms(0.1), signal.velocity_from_rms(0.2)
    ]


def _drum_pattern(bpm: float, bars: int = 6, jitter_ms: float = 0.0, seed: int = 11) -> np.ndarray:
    """A boombap-ish kick/snare/hat loop at ``bpm`` (8th-note hats).

    ``jitter_ms`` adds human-like timing noise. A mathematically exact
    pattern with 8th-note granularity is genuinely octave-ambiguous (both the
    true and the doubled grid fit perfectly); real performances aren't — the
    jitter spreads over a smaller step on the doubled grid, which is exactly
    the evidence the octave scorer uses.
    """
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    events: list[tuple[float, np.ndarray]] = []

    def j() -> float:
        return float(rng.normal(0.0, jitter_ms / 1000.0)) if jitter_ms else 0.0

    for b in range(bars):
        base = b * 4 * beat
        events += [(base + s * beat + j(), _kick_sample()) for s in (0, 2)]
        events += [(base + s * beat + j(), _snare_sample()) for s in (1, 3)]
        events += [(base + s * beat + j(), _hat_sample()) for s in (0.5, 1.5, 2.5, 3.5)]
    return _place(events, bars * 4 * beat + 0.3)


@pytest.mark.parametrize("bpm", [70, 84, 100, 120])
def test_detect_tempo_no_octave_error(tmp_path, monkeypatch, bpm):
    # beat_track reports ~2x on beatbox; the estimator must land on the right
    # octave and refine to within the +-3 BPM eval tolerance. 8 ms of human
    # jitter makes the octave decidable from grid evidence (see _drum_pattern).
    monkeypatch.setattr(drum_classify, "_MODEL", None)
    y = _drum_pattern(bpm, jitter_ms=8.0)
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
