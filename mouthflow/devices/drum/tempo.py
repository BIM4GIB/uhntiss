"""Beatbox-tuned tempo estimation + phase-aware quantization (drum-specific).

``librosa.beat.beat_track`` double-counts on beatbox (octave error), so the
drum device estimates tempo from its kept onsets: an onset-strength base,
octave-disambiguated against the inter-onset grid, with a confidence the
transcriber uses to gate quantization. The grid is then phase-aligned to the
performer's lead-in so snapping pulls hits toward the played timing.

(Extracted from the original ``transcribe.py`` during the umbrella refactor;
the generic voices use the simpler ``signal.detect_tempo``.)
"""

from __future__ import annotations

import librosa
import numpy as np

# Tempo estimation / quantisation gating.
_BPM_MIN, _BPM_MAX = 60.0, 200.0  # plausible beatbox tempo octaves
_BPM_PREF = (80.0, 150.0)  # preferred band — the octave prior nudges here
_QUANT_CONF_MIN = 0.5  # below this we emit raw onset times (don't trust tempo)


def _detect_tempo(y: np.ndarray, sr: int, onset_times: np.ndarray) -> tuple[float, float]:
    """Estimate (tempo_bpm, confidence) robustly for beatbox.

    ``librosa.beat.beat_track`` consistently reports ~2x the true tempo on
    beatbox (octave error). We take a base estimate from the onset-strength
    tempogram — robust to missing/extra hits — then disambiguate the octave
    against how tightly the onsets fall on each candidate's 16th grid, with a
    preference for the human band and the inter-onset subdivision lattice.

    Confidence ∈ [0, 1] reflects grid-fit tightness and the separation between
    the chosen octave and its runner-up; the caller gates quantisation on it.
    """
    onsets = np.asarray(onset_times, dtype=float)

    try:
        oenv = librosa.onset.onset_strength(y=y, sr=sr)
        base = float(np.median(librosa.feature.tempo(onset_envelope=oenv, sr=sr, aggregate=None)))
    except Exception:  # pragma: no cover — degenerate/empty signal
        base = 0.0

    ioi_bpm = _ioi_beat_bpm(onsets)
    if base <= 0 and ioi_bpm:
        base = ioi_bpm
    if base <= 0 or not np.isfinite(base):
        return 120.0, 0.0  # nothing to go on — preserve the old silent default

    candidates = sorted({round(base * m, 1) for m in (0.5, 1.0, 2.0) if _BPM_MIN <= base * m <= _BPM_MAX})
    if not candidates:
        return float(np.clip(base, _BPM_MIN, _BPM_MAX)), 0.0

    scored = sorted((_octave_score(b, onsets, ioi_bpm), b) for b in candidates)
    best_score, best = scored[0]

    tightness = float(np.clip(1.0 - 2.5 * _grid_fit(onsets, best), 0.0, 1.0))
    margin = float(np.clip((scored[1][0] - best_score) / 0.15, 0.0, 1.0)) if len(scored) > 1 else 1.0
    confidence = float(np.clip(0.5 * tightness + 0.5 * margin, 0.0, 1.0))
    return best, confidence


def _refine_tempo(onsets: np.ndarray, coarse_bpm: float, span: float = 4.0, step: float = 0.02) -> float:
    """Sub-BPM refine: minimise the grid residual over ±``span`` of ``coarse_bpm``.

    The coarse estimate has the right octave but only ±a few BPM; at the true
    tempo the onsets share a single phase (tight residual) while a small tempo
    error makes the grid drift across the clip (loose residual), so the residual
    has a sharp minimum at the played tempo.
    """
    if onsets.size < 4:
        return coarse_bpm
    lo = max(_BPM_MIN, coarse_bpm - span)
    hi = min(_BPM_MAX, coarse_bpm + span)
    grid = np.arange(lo, hi + 1e-9, step)
    return float(min(grid, key=lambda b: _grid_fit(onsets, b)))


def _ioi_beat_bpm(onsets: np.ndarray) -> float | None:
    """Modal inter-onset interval → BPM, as an octave anchor (None if sparse)."""
    if onsets.size < 5:
        return None
    iois = np.diff(np.sort(onsets))
    iois = iois[iois > 0.05]  # drop flams / double-trips
    if iois.size < 4:
        return None
    bins = np.arange(0.05, 0.80, 0.01)  # 10 ms bins
    hist, _ = np.histogram(iois, bins=bins)
    if hist.max() == 0:
        return None
    centre = (bins[hist.argmax()] + bins[hist.argmax() + 1]) / 2
    return 60.0 / centre


def _grid_fit(onsets: np.ndarray, bpm: float) -> float:
    """Mean distance (in steps) of onsets to the best-phase 16th grid, ∈ [0, 0.5].

    Phase-invariant: a constant lead-in offset is fitted out via the circular
    mean, so this measures only how *gridded* the playing is at ``bpm``. A
    too-fine (e.g. doubled) tempo spreads the same jitter over a smaller step,
    which raises the score — exactly the octave signal we want.
    """
    if onsets.size == 0:
        return 0.5
    step = 60.0 / bpm / 4.0
    frac = (onsets / step) % 1.0
    phi = np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi)
    d = np.abs(((frac - phi + 0.5) % 1.0) - 0.5)
    return float(np.mean(d))


def _octave_score(bpm: float, onsets: np.ndarray, ioi_bpm: float | None) -> float:
    """Lower is better: grid-fit + out-of-band penalty + IOI-lattice misfit."""
    score = _grid_fit(onsets, bpm)
    if not (_BPM_PREF[0] <= bpm <= _BPM_PREF[1]):
        score += 0.10
    if ioi_bpm:
        r = bpm / ioi_bpm
        score += 0.5 * min(abs(r * k - round(r * k)) for k in (1, 0.5, 0.25, 2))
    return score


def _grid_phase(onsets: np.ndarray, bpm: float) -> float:
    """Fractional phase (∈ [-0.5, 0.5] of a 16th step) of the played grid."""
    onsets = np.asarray(onsets, dtype=float)
    if onsets.size == 0:
        return 0.0
    step = 60.0 / bpm / 4.0
    frac = (onsets / step) % 1.0
    return float(np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi))


def _quantise_grid(t_s: float, tempo_bpm: float, phase: float) -> float:
    """Snap to a 16th grid whose lines sit at ``(n + phase) * step``.

    ``phase`` (from :func:`_grid_phase`) aligns the grid to the performer's
    lead-in so snapping pulls hits toward the played timing rather than an
    arbitrary phase-0 grid. ``phase=0`` reduces to a plain 16th snap.
    """
    step = 60.0 / tempo_bpm / 4.0
    return (round(t_s / step - phase) + phase) * step
