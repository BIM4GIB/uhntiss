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
_ALLOWED_BARS = (4, 8, 16)


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
        hist[n.midi_note % 12] += weight
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


def correct_notes(notes, *, key: str | None = None, scale: str | None = None):
    """Snap notes to a scale. Auto-detect the key unless ``key`` is forced.
    ``scale`` overrides the mode (e.g. 'dorian'). Returns ``(new_notes, label)``."""
    if not notes:
        return notes, None
    if key is not None:
        tonic = _parse_key(key)
        mode = scale or "major"
    else:
        tonic, det_mode = detect_key(notes)
        mode = scale or det_mode
    intervals = SCALES.get(mode, SCALES["major"])
    out = [replace(n, midi_note=snap_to_scale(n.midi_note, tonic, intervals)) for n in notes]
    return out, f"{_PC_TO_NAME[tonic]} {mode}"


# --- bar fit / loop sizing --------------------------------------------------

def fit_to_bars(notes, tempo_bpm: float, spec, beats_per_bar: int = 4):
    """Size the clip to a whole bar count and clamp note overhang.

    ``spec``: ``"off"`` -> ``(notes, None)`` (keep whatever bars the caller had);
    ``"auto"`` -> round the take UP to the nearest of 4/8/16 (multiple of 8 past
    16) so nothing is cut; an int -> force that many bars (notes past the end are
    dropped, a note straddling the end is clamped). Returns ``(notes, n_bars)``.
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
        if n.duration_s is not None and n.time_s + n.duration_s > limit_s:
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

    hits = t.hits
    key_label = None
    if correct and hits:
        hits, key_label = correct_notes(hits, key=key, scale=scale)

    hits, n_bars = fit_to_bars(hits, t.tempo_bpm, bars)

    signal.write_midi(t.midi_path, hits, t.tempo_bpm, channel=0)
    new_bars = float(n_bars) if n_bars else t.bars
    return replace(t, hits=hits, bars=new_bars), {"key": key_label, "bars": n_bars}
