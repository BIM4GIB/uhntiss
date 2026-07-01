"""Tests for post-transcription refinement: note correction + bar-fit/loop."""

from __future__ import annotations

from pathlib import Path

import mido

from mouthflow import signal
from mouthflow.devices.base import ClipMode
from mouthflow.refine import (
    correct_notes,
    detect_key,
    fit_to_bars,
    refine_transcription,
    snap_to_scale,
)
from mouthflow.schemas import NoteEvent, Transcription


def _notes(pitches, *, step=0.5, dur=0.45):
    return [NoteEvent(i * step, p, 90, dur) for i, p in enumerate(pitches)]


# --- note correction --------------------------------------------------------

def test_detect_key_major_with_emphasized_root():
    # C major, root held longer (a real performance emphasizes the tonic).
    notes = [NoteEvent(0.0, 60, 90, 1.5)] + _notes([62, 64, 65, 67, 69, 71])
    tonic, mode = detect_key(notes)
    assert (tonic, mode) == (0, "major")


def test_snap_to_scale_moves_at_most_one_semitone_for_diatonic():
    # Every chromatic pitch -> nearest C-major degree, never more than 1 semitone.
    cmaj = [0, 2, 4, 5, 7, 9, 11]
    for midi in range(48, 84):
        snapped = snap_to_scale(midi, 0, cmaj)
        assert abs(snapped - midi) <= 1
        assert snapped % 12 in cmaj


def test_correct_notes_forced_key_snaps_accidentals():
    # C# and F# (out of C major) snap down to C and F; in-scale notes unchanged.
    detuned = _notes([60, 61, 64, 66, 67, 71])  # C C# E F# G B
    out, label = correct_notes(detuned, key="C", scale="major")
    assert [n.midi_note for n in out] == [60, 60, 64, 65, 67, 71]
    assert label == "C major"


def test_correct_notes_keeps_confident_out_of_scale_note():
    # F#4 is out of C major. A confident note is trusted (kept); an uncertain
    # one is snapped. This is the fix for correction corrupting a clearly-hummed
    # chromatic bass note (e.g. a confident E snapped down to D#).
    confident = [NoteEvent(0.0, 66, 90, 0.5, confidence=0.95)]
    out, _ = correct_notes(confident, key="C", scale="major")
    assert out[0].midi_note == 66  # kept

    uncertain = [NoteEvent(0.0, 66, 90, 0.5, confidence=0.4)]
    out, _ = correct_notes(uncertain, key="C", scale="major")
    assert out[0].midi_note in (65, 67)  # snapped to F or G


def test_correct_notes_preserves_octave():
    out, _ = correct_notes(_notes([73]), key="C", scale="major")  # C#5 -> C5
    assert out[0].midi_note == 72


def test_correct_notes_empty_is_noop():
    out, label = correct_notes([])
    assert out == [] and label is None


# --- bar fit ----------------------------------------------------------------

def test_fit_auto_rounds_up_to_nearest_allowed():
    # ~5.4 bars of content at 120 BPM (2s/bar) -> 8 bars, nothing dropped.
    take = _notes([45, 47, 45, 47, 45], step=2.0)  # last onset 8.0s = 4 bars
    kept, n = fit_to_bars(take, 120.0, "auto")
    assert n == 8
    assert len(kept) == len(take)


def test_fit_explicit_drops_notes_past_end():
    take = _notes([45, 45, 45, 45, 45], step=2.0)  # onsets 0,2,4,6,8s
    kept, n = fit_to_bars(take, 120.0, 4)  # 4 bars = 8s
    assert n == 4
    assert [round(k.time_s, 1) for k in kept] == [0.0, 2.0, 4.0, 6.0]  # 8.0s dropped


def test_fit_clamps_overhang():
    # A note starting at 7s with a 3s tail in a 4-bar (8s) clip is clamped to 1s.
    take = [NoteEvent(7.0, 45, 90, 3.0)]
    kept, n = fit_to_bars(take, 120.0, 4)
    assert n == 4 and len(kept) == 1
    assert abs(kept[0].duration_s - 1.0) < 1e-6


def test_fit_off_keeps_everything():
    take = _notes([45, 47, 45], step=2.0)
    kept, n = fit_to_bars(take, 120.0, "off")
    assert n is None and len(kept) == len(take)


# --- orchestrator -----------------------------------------------------------

def _transcription(tmp_path: Path, pitches, *, channel=0, bars=5.4) -> Transcription:
    midi = tmp_path / "t.mid"
    hits = _notes(pitches, step=2.0)
    signal.write_midi(midi, hits, 120.0, channel=channel)
    return Transcription(midi_path=midi, tempo_bpm=120.0, bars=bars, hits=hits)


def test_refine_drums_passes_through_untouched(tmp_path):
    t = _transcription(tmp_path, [36, 38, 42], channel=9)
    out, meta = refine_transcription(t, ClipMode.PERCUSSIVE)
    assert out is t and meta == {"key": None, "bars": None}


def test_refine_pitched_corrects_and_fits_and_rewrites_midi(tmp_path):
    # An out-of-C-major take; auto-fit should land on 8 bars and rewrite MIDI.
    t = _transcription(tmp_path, [60, 61, 64, 66, 67])  # onsets every 2s -> 4 bars
    out, meta = refine_transcription(
        t, ClipMode.MONOPHONIC, key="C", scale="major", bars="auto"
    )
    assert meta["key"] == "C major"
    assert meta["bars"] == 8
    assert out.bars == 8.0
    # C# -> C, F# -> F in the rewritten notes
    assert [n.midi_note for n in out.hits] == [60, 60, 64, 65, 67]
    # MIDI on disk reflects the corrected pitches, on channel 0
    ons = [m for tr in mido.MidiFile(out.midi_path).tracks for m in tr
           if m.type == "note_on" and m.velocity > 0]
    assert sorted(m.note for m in ons) == [60, 60, 64, 65, 67]
    assert all(m.channel == 0 for m in ons)
