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

# Groove: how hard quantisation pulls a hit onto the swing-aware grid.
# 1.0 = snap fully (default), 0.0 = raw. The market's "quantise flattens my
# groove" complaint is about SYSTEMATIC feel — swing — which now lives in the
# grid itself (_swing_frac shifts the off-beat lines), so full snap keeps the
# shuffle while removing random jitter: closest to "what you meant". Lower
# values keep a fraction of the per-hit jitter too.
_QUANT_STRENGTH = 1.0
_SWING_MIN_OFF = 4      # need at least this many onsets in an off bucket
# Swing floors, in fractions of a 16th step — calibrated on the fixture takes:
# performers systematically DRAG their off-beats by articulation (~0.17 step
# measured on a straight-intent take), which is laziness to correct, not feel
# to preserve. Real shuffle starts around 55% swing (0.2 step for off-8ths,
# 0.1-0.2 for off-16ths). Below the floor -> treat as straight.
_SWING_NOISE_8 = 0.20
_SWING_NOISE_16 = 0.12
# Cap at the fold-over limit: past ~0.5 step the swung off-8th is closer to
# the NEXT 16th line and residual bucketing can't see it anyway. (Full
# triplet shuffle = 0.67 step is a known blind spot.)
_SWING_MAX = 0.45


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
        # Distance-scaled nudge with NO flat floor: a flat +0.10 (or even
        # +0.03) rivals the real grid-evidence separation (~0.03-0.05 at
        # human jitter) and structurally doubled true tempos just below the
        # band (70 -> 140, where 140 sits comfortably in-band). Just outside
        # the band the prior should be nearly silent and let the grid decide.
        dist = min(abs(bpm - _BPM_PREF[0]), abs(bpm - _BPM_PREF[1])) / _BPM_PREF[0]
        score += min(0.08 * dist, 0.10)
    if ioi_bpm:
        r = bpm / ioi_bpm
        # k=4 lets a 16th-note modal IOI certify the true tempo (r=0.25)
        # instead of unfairly penalising dense trap-style takes.
        score += 0.5 * min(abs(r * k - round(r * k)) for k in (1, 0.5, 0.25, 2, 4))
        # Density plausibility: a candidate claiming the modal event spacing
        # is finer than 8th notes is usually the HALVED octave (the coarser
        # grid always step-fraction-fits better, and k=4 alone would bless
        # it). Graded, so a genuinely 16th-dense take only pays a little.
        s = ioi_bpm / bpm
        if s > 3.0:
            score += 0.03 * (s - 3.0)
    return score


def _swing_frac(onsets: np.ndarray, bpm: float, phase: float) -> tuple[float, float]:
    """(swing8, swing16): mean lag of off-8ths / off-16ths, in 16th steps.

    Swing lives at two levels and they are NOT the same thing: a classic
    shuffle delays the off-beat EIGHTHS (16th-grid line index ≡ 2 mod 4 —
    the "and"s), hip-hop swing delays the off-SIXTEENTHS (odd indices — the
    "e"s and "a"s). Measuring only odd-vs-even 16ths is blind to the common
    8th shuffle (the delayed "and"s land on EVEN indices). Each component is
    gated independently so noise can't invent a shuffle: ``_SWING_MIN_OFF``
    onsets in its own off bucket, a jitter floor, and a cap (a huge "swing"
    usually means the grid itself is wrong). Beats (index ≡ 0 mod 4) anchor
    both components.
    """
    onsets = np.asarray(onsets, dtype=float)
    if onsets.size == 0 or bpm <= 0:
        return 0.0, 0.0
    step = 60.0 / bpm / 4.0
    res: dict[int, list[float]] = {0: [], 1: [], 2: [], 3: []}
    for t in onsets:
        pos = t / step - phase
        idx = int(round(pos))
        res[idx % 4].append(pos - idx)  # residual in steps
    on = float(np.mean(res[0])) if res[0] else 0.0

    def lean(vals: list[float], floor: float) -> float:
        if len(vals) < _SWING_MIN_OFF:
            return 0.0
        arr = np.asarray(vals, dtype=float) - on
        s = float(arr.mean())
        # Consistency gate: a real shuffle is SYSTEMATIC — every off-beat
        # leans the same way. A mean assembled from scattered mixed-sign
        # residuals (measured on a real take: +0.14/-0.02 buckets pooling to
        # a fake +0.11 "swing") must not pass.
        sem = float(arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else 0.0
        if abs(s) < floor or abs(s) <= 2.0 * sem:
            return 0.0
        return float(np.clip(s, -_SWING_MAX, _SWING_MAX))

    return lean(res[2], _SWING_NOISE_8), lean(res[1] + res[3], _SWING_NOISE_16)


def _grid_phase(onsets: np.ndarray, bpm: float) -> float:
    """Fractional phase (∈ [-0.5, 0.5] of a 16th step) of the played grid."""
    onsets = np.asarray(onsets, dtype=float)
    if onsets.size == 0:
        return 0.0
    step = 60.0 / bpm / 4.0
    frac = (onsets / step) % 1.0
    return float(np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi))


def _quantise_grid(
    t_s: float,
    tempo_bpm: float,
    phase: float,
    swing8: float = 0.0,
    swing16: float = 0.0,
    strength: float = 1.0,
) -> float:
    """Pull ``t_s`` toward a (possibly swung) 16th grid at ``(n + phase) * step``.

    ``phase`` (from :func:`_grid_phase`) aligns the grid to the performer's
    lead-in so snapping pulls hits toward the played timing rather than an
    arbitrary phase-0 grid. ``swing8``/``swing16`` (from :func:`_swing_frac`,
    in fractions of a step) shift the off-8th / off-16th grid lines late, so
    a shuffled performance snaps to its own shuffle instead of being
    straightened. ``strength`` blends: 1.0 = hard snap (the historic
    behaviour), 0.0 = raw performed time.

    Clamped at 0: a negative phase can place the first grid cell's target at
    a negative time, which MIDI (and mido) cannot represent.
    """
    step = 60.0 / tempo_bpm / 4.0
    idx = round(t_s / step - phase)
    if idx % 2:
        shift = swing16
    elif idx % 4 == 2:
        shift = swing8
    else:
        shift = 0.0
    target = (idx + phase + shift) * step
    return max(0.0, t_s + strength * (target - t_s))
