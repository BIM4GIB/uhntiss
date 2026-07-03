"""Post-transcription refinement for the pitched voices: note correction
(autotune-style scale snap) and bar-fit / loop sizing.

Runs AFTER a transcriber produces notes, BEFORE planning. Two operations, both
pitched-only (drums are never pitch-corrected and keep their own grid):

- ``correct_notes`` — snap each note to a musical scale so a non-singer's wobbly
  take lands in tune. The key is **auto-detected** from the performance
  (Krumhansl-Schmuckler pitch-class correlation), or forced via ``key``/``scale``.
  Snapping to the nearest major/minor degree moves a note by at most one
  semitone, so it tightens pitch without rewriting the melody.
- ``fit_to_bars`` — size the clip to a whole bar count (4/8/16, or ``auto`` =
  round the take **up** to the nearest so nothing is cut) and clamp any note
  overhang, so the clip loops cleanly on the project grid.

Pitch/timing edits mean the MIDI is rewritten; ``refine_transcription`` does that
on channel 0 (pitched) and returns an updated ``Transcription`` plus a small
metadata dict (detected key, chosen bar count) for logging.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from mouthflow import signal
from mouthflow.devices.base import ClipMode
from mouthflow.schemas import Transcription

# --- note correction --------------------------------------------------------

_PC_TO_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NOTE_TO_PC = {n: i for i, n in enumerate(_PC_TO_NAME)}
_NOTE_TO_PC.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})

# Krumhansl-Schmuckler key profiles (major / minor).
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],  # natural minor
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "chromatic": list(range(12)),
}
_ALLOWED_BARS = (1, 2, 4, 8, 16)


def _parse_key(s: str) -> int:
    """'c' / 'C' / 'f#' / 'Bb' -> pitch class 0-11."""
    s = s.strip()
    norm = s[0].upper() + s[1:]
    if norm not in _NOTE_TO_PC:
        raise ValueError(f"unknown key {s!r} (try C, F#, Bb, ...)")
    return _NOTE_TO_PC[norm]


def _pitch_class_histogram(notes) -> np.ndarray:
    hist = np.zeros(12)
    for n in notes:
        weight = n.duration_s if (n.duration_s and n.duration_s > 0) else 0.1
        # Confident notes define the key; uncertain ones shouldn't sway it.
        conf = n.confidence if n.confidence is not None else 1.0
        hist[n.midi_note % 12] += weight * conf
    return hist


def detect_key(notes) -> tuple[int, str]:
    """(tonic pitch-class, 'major'|'minor') by Krumhansl-Schmuckler correlation.
    Falls back to C major when there's too little pitch signal to be sure."""
    hist = _pitch_class_histogram(notes)
    if hist.sum() <= 0 or np.count_nonzero(hist) < 2:
        return 0, "major"
    best: tuple[float, int, str] | None = None
    for tonic in range(12):
        for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            rotated = np.roll(profile, tonic)
            corr = float(np.corrcoef(hist, rotated)[0, 1])
            if best is None or corr > best[0]:
                best = (corr, tonic, mode)
    return best[1], best[2]


def snap_to_scale(midi: int, tonic: int, intervals) -> int:
    """Snap ``midi`` to the nearest scale degree, preserving the octave."""
    scale_pcs = sorted({(tonic + i) % 12 for i in intervals})
    pc = midi % 12
    target = min(scale_pcs, key=lambda s: min((pc - s) % 12, (s - pc) % 12))
    base = midi - pc + target
    return min((base - 12, base, base + 12), key=lambda m: abs(m - midi))


def correct_notes(notes, *, key: str | None = None, scale: str | None = None, keep_confident: float = 0.75):
    """Snap notes to a scale. Auto-detect the key unless ``key`` is forced.
    ``scale`` overrides the mode (e.g. 'dorian').

    Only notes pyin is UNSURE about are snapped: a note whose ``confidence`` is
    >= ``keep_confident`` is left as the performer pitched it. Basslines are
    often chromatic, so forcing every note into one scale corrupts the clearly-
    articulated notes; nudging only the wobbly ones fixes pitch without
    rewriting the line. Returns ``(new_notes, label)``."""
    if not notes:
        return notes, None
    if key is not None:
        tonic = _parse_key(key)
        mode = scale or "major"
    else:
        tonic, det_mode = detect_key(notes)
        mode = scale or det_mode
    intervals = SCALES.get(mode, SCALES["major"])
    out = []
    for n in notes:
        confident = n.confidence is not None and n.confidence >= keep_confident
        if confident:
            out.append(n)  # trust a clearly-pitched note over the scale guess
        else:
            out.append(replace(n, midi_note=snap_to_scale(n.midi_note, tonic, intervals)))
    return out, f"{_PC_TO_NAME[tonic]} {mode}"


# --- bar fit / loop sizing --------------------------------------------------

def trim_lead_bars(notes, tempo_bpm: float, beats_per_bar: int = 4):
    """Trim whole empty lead-in bars (a performer breathing before starting).

    Shifts by whole bars only, so every note keeps its position within the
    bar. Returns ``(notes, lead_bars)`` — the shift is reported so callers can
    move anything else living on the same timeline (the drone's automation
    envelope) by the same amount.
    """
    sec_per_bar = beats_per_bar * 60.0 / tempo_bpm if tempo_bpm > 0 else 0.0
    first_s = min((n.time_s for n in notes), default=0.0)
    if sec_per_bar <= 0 or first_s <= 0:
        return notes, 0
    lead_bars = int((first_s + 1e-6) / sec_per_bar)
    if lead_bars <= 0:
        return notes, 0
    shift = lead_bars * sec_per_bar
    return [replace(n, time_s=n.time_s - shift) for n in notes], lead_bars


def fit_to_bars(notes, tempo_bpm: float, spec, beats_per_bar: int = 4, *, sustain: bool = False):
    """Size the clip to a whole bar count and clamp note overhang.

    ``spec``: ``"off"`` -> ``(notes, None)`` (keep whatever bars the caller had);
    ``"auto"`` -> round the take UP to the nearest of 1/2/4/8/16 (multiple of 8
    past 16) so nothing is cut; an int -> force that many bars (notes past the
    end are dropped, a note straddling the end is clamped).

    Whole empty *lead-in* bars (dead air before the first note) are trimmed
    first, so a performer who breathes for a bar before starting doesn't loop
    that silence. ``sustain=True`` (the drone) extends every kept note to the
    loop end instead of clamping, so a forced bar count never leaves silent
    bars in a clip whose whole point is to ring continuously.

    Returns ``(notes, n_bars)``.
    """
    if spec == "off":
        return notes, None
    # Normalize a forced bar count; anything unrecognized falls back to auto so a
    # stray value never raises mid-pipeline.
    if spec != "auto":
        try:
            spec = int(spec)
            if spec <= 0:
                spec = "auto"
        except (TypeError, ValueError):
            spec = "auto"
    sec_per_bar = beats_per_bar * 60.0 / tempo_bpm if tempo_bpm > 0 else 0.0
    notes, _ = trim_lead_bars(notes, tempo_bpm, beats_per_bar)

    end_s = max((n.time_s + (n.duration_s or 0.0) for n in notes), default=0.0)
    content_bars = end_s / sec_per_bar if sec_per_bar > 0 else 0.0

    if spec == "auto":
        n_bars = next((b for b in _ALLOWED_BARS if b >= content_bars - 1e-6), None)
        if n_bars is None:
            n_bars = int(math.ceil(max(content_bars, 1) / 8.0) * 8)
    else:
        n_bars = int(spec)

    limit_s = n_bars * sec_per_bar
    kept = []
    for n in notes:
        if n.time_s >= limit_s - 1e-6:
            continue  # starts past the loop end
        if sustain:
            n = replace(n, duration_s=max(0.05, limit_s - n.time_s))  # ring to the loop end
        elif n.duration_s is not None and n.time_s + n.duration_s > limit_s:
            n = replace(n, duration_s=max(0.05, limit_s - n.time_s))  # clamp overhang
        kept.append(n)
    return kept, n_bars


# --- orchestrator -----------------------------------------------------------

def refine_transcription(
    t: Transcription,
    clip_mode: ClipMode,
    *,
    correct: bool = True,
    key: str | None = None,
    scale: str | None = None,
    bars="auto",
) -> tuple[Transcription, dict]:
    """Apply note correction + bar-fit to a pitched transcription, rewrite its
    MIDI, and return ``(updated_transcription, meta)``. Drums pass through
    untouched (they keep their own grid and are never pitch-corrected)."""
    pitched = clip_mode in (ClipMode.MONOPHONIC, ClipMode.SUSTAINED)
    if not pitched:
        return t, {"key": None, "bars": None}
    sustained = clip_mode is ClipMode.SUSTAINED

    hits = t.hits
    key_label = None
    key_skipped = None
    # Scale-snap is for melodic lines. A drone is one or a few *held* pitches —
    # too little pitch-class signal for key detection (a single-pitch drone
    # always trips the C-major fallback and gets silently transposed), and the
    # held pitch IS the performance. Never snap sustained clips.
    if correct and hits:
        if sustained:
            if key or scale:
                key_skipped = "sustained clip — held pitch kept verbatim"
        else:
            hits, key_label = correct_notes(hits, key=key, scale=scale)

    # Trim before fitting so the lead-bar shift is known here — the automation
    # envelope lives on the same timeline and must move with the notes.
    lead_bars = 0
    if bars != "off":
        hits, lead_bars = trim_lead_bars(hits, t.tempo_bpm)
    hits, n_bars = fit_to_bars(hits, t.tempo_bpm, bars, sustain=sustained)

    automation = _refit_automation(t.automation, lead_bars, n_bars)

    signal.write_midi(t.midi_path, hits, t.tempo_bpm, channel=0)
    new_bars = float(n_bars) if n_bars else t.bars
    return (
        replace(t, hits=hits, bars=new_bars, automation=automation),
        {"key": key_label, "bars": n_bars, "key_skipped": key_skipped},
    )


def _refit_automation(envelopes, lead_bars: int, n_bars, beats_per_bar: int = 4):
    """Shift envelopes left by the trimmed lead-in and clamp to the loop end.

    Steps are ``(time_in_beats, value)`` on the clip's timeline; steps past
    the loop end are dropped (Live holds an envelope's last value anyway).
    """
    if not envelopes or (lead_bars <= 0 and not n_bars):
        return envelopes
    shift = lead_bars * float(beats_per_bar)
    limit = n_bars * float(beats_per_bar) if n_bars else None
    out = []
    for env in envelopes:
        steps = [(t_b - shift, v) for t_b, v in env.steps if t_b - shift >= -1e-9]
        if limit is not None:
            steps = [(t_b, v) for t_b, v in steps if t_b <= limit + 1e-9]
        if steps:
            out.append(env.model_copy(update={"steps": [(max(0.0, t_b), v) for t_b, v in steps]}))
    return out
