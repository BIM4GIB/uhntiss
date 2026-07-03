"""Tests for post-transcription refinement: note correction + bar-fit/loop."""

from __future__ import annotations

from pathlib import Path

import mido
import pytest

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
    # A note starting at 7s with a 3s tail in a 4-bar (8s) clip is clamped to
    # 1s. (The note at 0 anchors bar 1 so lead-in trimming stays out of play.)
    take = [NoteEvent(0.0, 45, 90, 1.0), NoteEvent(7.0, 45, 90, 3.0)]
    kept, n = fit_to_bars(take, 120.0, 4)
    assert n == 4 and len(kept) == 2
    assert abs(kept[1].duration_s - 1.0) < 1e-6


def test_fit_off_keeps_everything():
    take = _notes([45, 47, 45], step=2.0)
    kept, n = fit_to_bars(take, 120.0, "off")
    assert n is None and len(kept) == len(take)


def test_fit_auto_allows_one_and_two_bar_loops():
    # A 1-bar riff must loop as 1 bar, not 4 bars with 3 bars of dead air.
    one_bar = _notes([45, 47, 45], step=0.5)  # ends 1.45s < 2s (1 bar at 120)
    kept, n = fit_to_bars(one_bar, 120.0, "auto")
    assert n == 1 and len(kept) == len(one_bar)

    two_bar = _notes([45, 47, 45, 47, 45], step=0.7)  # ends ~3.25s -> 2 bars
    kept, n = fit_to_bars(two_bar, 120.0, "auto")
    assert n == 2 and len(kept) == len(two_bar)


def test_fit_trims_whole_leadin_bars():
    # Performer breathes for a bar (2s at 120 BPM) before starting: the empty
    # lead-in bar is trimmed, in-bar positions preserved.
    take = [NoteEvent(2.1, 45, 90, 0.4), NoteEvent(3.1, 47, 90, 0.4)]
    kept, n = fit_to_bars(take, 120.0, "auto")
    assert [round(k.time_s, 2) for k in kept] == [0.1, 1.1]
    assert n == 1


def test_fit_sustain_extends_notes_to_loop_end():
    # Drone: every kept note rings to the loop end, so a forced bar count
    # never leaves silent bars in the loop.
    take = [NoteEvent(0.0, 48, 90, 3.0), NoteEvent(1.0, 55, 90, 2.0)]
    kept, n = fit_to_bars(take, 120.0, 2, sustain=True)  # 2 bars = 4s
    assert n == 2
    assert [round(k.time_s + k.duration_s, 2) for k in kept] == [4.0, 4.0]


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


def test_refine_shifts_automation_with_leadin_trim_and_clamps_to_loop(tmp_path):
    # The drone's loudness envelope lives on the same beat timeline as the
    # notes: trimming a lead-in bar must shift it, and a forced bar count must
    # clamp it — otherwise the automation plays against the wrong bars.
    from mouthflow.schemas import AutomationEnvelope

    midi = tmp_path / "d.mid"
    # One bar (2s at 120) of breath, then a held note for 6s (ends 8s = bar 4).
    hits = [NoteEvent(2.0, 48, 90, 6.0, confidence=0.8)]
    signal.write_midi(midi, hits, 120.0, channel=0)
    env = AutomationEnvelope(parameter="Macro 1", steps=[(0.0, 0.0), (4.0, 0.5), (16.0, 1.0)])
    t = Transcription(midi_path=midi, tempo_bpm=120.0, bars=4.0, hits=hits, automation=[env])

    out, meta = refine_transcription(t, ClipMode.SUSTAINED, correct=False, bars=2)
    assert meta["bars"] == 2
    # note shifted left one bar and extended to the 2-bar loop end
    assert out.hits[0].time_s == pytest.approx(0.0)
    assert out.hits[0].duration_s == pytest.approx(4.0)
    # envelope shifted by 4 beats and clamped to the 8-beat loop:
    # (0,0.0) drops (negative), (4,0.5) -> (0,0.5), (16,1.0) past 8 drops.
    assert out.automation[0].steps == [(0.0, 0.5)]


def test_refine_sustained_never_snaps_pitch_and_fills_the_loop(tmp_path):
    # A single-pitch drone has too little pitch-class signal for key detection
    # (it used to trip the C-major fallback and get silently transposed).
    # SUSTAINED clips must keep the hummed pitch verbatim and ring to the end.
    midi = tmp_path / "d.mid"
    hits = [NoteEvent(0.0, 42, 90, 3.0, confidence=0.6)]  # F#2, out of C major
    signal.write_midi(midi, hits, 120.0, channel=0)
    t = Transcription(midi_path=midi, tempo_bpm=120.0, bars=1.5, hits=hits)

    out, meta = refine_transcription(t, ClipMode.SUSTAINED, correct=True, bars="auto")
    assert meta["key"] is None  # no scale-snap ran
    assert out.hits[0].midi_note == 42  # the hummed pitch, verbatim
    assert meta["bars"] == 2
    # rings to the loop end (2 bars = 4s at 120)
    assert out.hits[0].time_s + out.hits[0].duration_s == pytest.approx(4.0)


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
