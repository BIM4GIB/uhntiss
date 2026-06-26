"""Tests for the pitched (bass/lead) transcriber.

pyin on a clean synthetic sine is reliable, so these assert real pitch accuracy
(not just pipeline shape): a known tone -> the right MIDI note, on channel 0,
with a real (non-1/32) duration, plus octave-snapping into the target register.
"""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from mouthflow.devices.bass.device import BASS_CONFIG
from mouthflow.devices.lead.device import LEAD_CONFIG
from mouthflow.devices.pitched import (
    PitchedTranscriber,
    _mode,
    _note_name,
    _snap_octave,
    pitched_plan_summary,
)

SR = 44_100


def _sine(freq: float, dur_s: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * dur_s)) / SR
    # Short raised-cosine fades avoid click transients fooling onset detection.
    y = amp * np.sin(2 * np.pi * freq * t)
    n_fade = int(0.005 * SR)
    ramp = np.linspace(0, 1, n_fade)
    y[:n_fade] *= ramp
    y[-n_fade:] *= ramp[::-1]
    return y.astype(np.float32)


def _write(path: Path, y: np.ndarray) -> Path:
    sf.write(path, y, SR, subtype="PCM_16")
    return path


def _note_ons(midi_path: Path):
    mid = mido.MidiFile(midi_path)
    return [m for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0]


def test_snap_octave_into_range():
    # A4 (69) snaps down into the E1..E3 bass window -> A2 (45).
    assert _snap_octave(69, 28, 52) == 45
    # Already in range -> unchanged.
    assert _snap_octave(40, 28, 52) == 40
    # Too low -> up an octave.
    assert _snap_octave(20, 28, 52) == 32


def test_mode_picks_most_common():
    assert _mode([45, 45, 46, 45]) == 45
    assert _mode([60, 61]) == 60  # tie -> smaller


def test_note_name_scientific():
    assert _note_name(60) == "C4"
    assert _note_name(45) == "A2"


def test_bass_transcribes_low_sine_to_correct_pitch(tmp_path):
    # 110 Hz == A2 == MIDI 45, squarely in the bass range.
    wav = _write(tmp_path / "a2.wav", _sine(110.0, 1.5))
    t = PitchedTranscriber(BASS_CONFIG).transcribe(wav)

    assert t.hits, "expected at least one note"
    assert all(h.midi_note == 45 for h in t.hits), [h.midi_note for h in t.hits]
    # Real, sustained durations — not the drum 1/32 default.
    assert all(h.duration_s is not None and h.duration_s > 0.1 for h in t.hits)

    ons = _note_ons(t.midi_path)
    assert ons, "expected a note_on"
    assert all(m.channel == 0 for m in ons), "pitched notes go on channel 0"


def test_bass_octave_snaps_a_high_hum(tmp_path):
    # 220 Hz == A3 == MIDI 57 (within bass's 40-400 Hz search). It's above the
    # E1-E3 target, so it snaps down an octave to A2 (45) — the "hummed an
    # octave high" case. (440 Hz would be above the bass fmax=400 by design.)
    wav = _write(tmp_path / "a3.wav", _sine(220.0, 1.2))
    t = PitchedTranscriber(BASS_CONFIG).transcribe(wav)
    assert t.hits
    assert all(h.midi_note == 45 for h in t.hits), [h.midi_note for h in t.hits]


def test_lead_transcribes_mid_sine_without_snapping(tmp_path):
    # 440 Hz == A4 == MIDI 69, inside the lead window -> stays 69.
    wav = _write(tmp_path / "lead.wav", _sine(440.0, 1.2))
    t = PitchedTranscriber(LEAD_CONFIG).transcribe(wav)
    assert t.hits
    assert all(h.midi_note == 69 for h in t.hits), [h.midi_note for h in t.hits]


def test_two_note_line_yields_two_pitches(tmp_path):
    # A2 (110 Hz) then C3 (~130.81 Hz, MIDI 48), back to back.
    y = np.concatenate([_sine(110.0, 0.8), _sine(130.81, 0.8)])
    wav = _write(tmp_path / "two.wav", y)
    t = PitchedTranscriber(BASS_CONFIG).transcribe(wav)
    pitches = [h.midi_note for h in t.hits]
    assert 45 in pitches and 48 in pitches, pitches
    # First note is the lower A2, in time order.
    ordered = sorted(t.hits, key=lambda h: h.time_s)
    assert ordered[0].midi_note == 45


def test_pitched_plan_summary_reports_range(tmp_path):
    wav = _write(tmp_path / "two.wav", np.concatenate([_sine(110.0, 0.8), _sine(130.81, 0.8)]))
    t = PitchedTranscriber(BASS_CONFIG).transcribe(wav)
    summary = pitched_plan_summary(t)
    assert summary["note_count"] >= 2
    assert summary["lowest_midi"] == 45
    assert summary["pitch_range"][0] == "A2"
