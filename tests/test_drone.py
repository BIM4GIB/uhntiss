"""Tests for the drone transcriber (sustained held note / hummed chord)."""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from mouthflow.devices.drone.contour import extract_loudness_contour
from mouthflow.devices.drone.transcriber import DroneTranscriber, drone_plan_summary

SR = 44_100


def _sine(freq: float, dur_s: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * dur_s)) / SR
    y = amp * np.sin(2 * np.pi * freq * t)
    nf = int(0.005 * SR)
    r = np.linspace(0, 1, nf)
    y[:nf] *= r
    y[-nf:] *= r[::-1]
    return y.astype(np.float32)


def _write(path: Path, y: np.ndarray) -> Path:
    sf.write(path, y, SR, subtype="PCM_16")
    return path


def _note_ons(midi_path: Path):
    mid = mido.MidiFile(midi_path)
    return [m for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0]


def test_single_sustained_tone_is_one_held_note(tmp_path):
    # 3s of C3 (130.81 Hz == MIDI 48).
    wav = _write(tmp_path / "hold.wav", _sine(130.81, 3.0))
    t = DroneTranscriber().transcribe(wav)

    assert len(t.hits) == 1, [h.midi_note for h in t.hits]
    note = t.hits[0]
    assert note.midi_note == 48
    # Sustained: a long note that fills most of the clip, not a 1/32 default.
    assert note.duration_s is not None and note.duration_s > 2.0
    assert t.bars >= 1
    # pyin confidence is recorded on the note (a clean sine is a sure pitch).
    assert note.confidence is not None and note.confidence > 0.5

    ons = _note_ons(t.midi_path)
    assert ons and all(m.channel == 0 for m in ons)


def test_hummed_sequence_becomes_a_held_chord(tmp_path):
    # C3, E3, G3 in sequence (a major triad hummed one note at a time).
    y = np.concatenate([_sine(130.81, 1.0), _sine(164.81, 1.0), _sine(196.00, 1.0)])
    wav = _write(tmp_path / "chord.wav", y)
    t = DroneTranscriber().transcribe(wav)

    pitches = sorted(h.midi_note for h in t.hits)
    assert pitches == [48, 52, 55], pitches

    # They ring together: every note sustains to (about) the same clip end.
    ends = [round(h.time_s + h.duration_s, 2) for h in t.hits]
    assert max(ends) - min(ends) < 0.05, ends

    summary = drone_plan_summary(t)
    assert summary["is_chord"] is True
    assert summary["voice_count"] == 3
    assert summary["pitches"] == ["C3", "E3", "G3"]


def test_loudness_contour_tracks_amplitude_swell(tmp_path):
    # A tone whose amplitude swells in the middle -> the contour peaks mid-clip.
    dur = 2.0
    t = np.arange(int(SR * dur)) / SR
    swell = np.sin(np.pi * t / dur)  # 0 -> 1 -> 0 over the clip
    y = (0.6 * swell * np.sin(2 * np.pi * 130.81 * t)).astype(np.float32)

    env = extract_loudness_contour(y, SR, tempo_bpm=120.0, n_steps=16)
    assert len(env.steps) == 16
    times = [s[0] for s in env.steps]
    values = [s[1] for s in env.steps]
    # times are non-decreasing beats; values are normalized [0, 1].
    assert times == sorted(times)
    assert all(0.0 <= v <= 1.0 for v in values)
    # the loudest step is near the middle, not the edges.
    peak = values.index(max(values))
    assert 4 <= peak <= 11, peak
    assert values[0] < 0.5 and values[-1] < 0.5


def test_drone_transcription_carries_automation(tmp_path):
    wav = _write(tmp_path / "hold.wav", _sine(130.81, 2.0))
    t = DroneTranscriber().transcribe(wav)
    assert t.automation, "drone should attach a contour envelope"
    assert t.automation[0].parameter == "Macro 1"
    assert len(t.automation[0].steps) > 0
