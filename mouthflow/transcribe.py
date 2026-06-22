"""Beatbox WAV -> drum MIDI + tempo.

v0.1 pipeline (spec §component 3):

1. Onset detection via librosa.onset.onset_detect(backtrack=True).
2. Per-onset 120ms window feature vector: spectral centroid, spectral
   flatness, zero-crossing rate, RMS, sub-100Hz energy ratio.
3. Hand-tuned heuristic classifier → GM drum note.
4. Tempo: onset-strength tempogram, octave-disambiguated against the
   inter-onset grid (librosa.beat.beat_track double-counts on beatbox).
   Returns a confidence so we only quantise when the tempo is trustworthy.
5. Quantise onsets to 16th notes on a grid *phase-aligned to the performance*
   — a phase-0 grid would shear every hit by the performer's lead-in offset.
6. Write MIDI via mido (GM drum map, channel 10 = MIDI channel 9).

Thresholds are sensible defaults. The 20-clip corpus is what tunes them.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import librosa
import mido
import numpy as np

from mouthflow.schemas import DrumHit, Transcription

GM_KICK = 36
GM_SNARE = 38
GM_HAT_CLOSED = 42
GM_HAT_OPEN = 46
GM_PERC = 39  # unused in v0.1 but reserved

DROP = -1  # sentinel returned by classify when we'd rather silence than guess

_WINDOW_S = 0.120
_SR = 44_100

# Onset peak-picking. Broadband envelope + default hop keep low mouth-kicks
# (an HF-emphasised envelope drops them). delta slightly above librosa's 0.07
# trims marginal false onsets; the wait floor suppresses double-triggers on a
# single transient — both well under a 16th note at any beatbox tempo. Tuning
# beyond this hurt on the corpus: with grid-snapping, onset *timing* precision
# no longer matters, only false-positive/negative count, which these control.
_ONSET_DELTA = 0.10
_ONSET_WAIT_S = 0.040

# Tempo estimation / quantisation gating.
_BPM_MIN, _BPM_MAX = 60.0, 200.0  # plausible beatbox tempo octaves
_BPM_PREF = (80.0, 150.0)  # preferred band — the octave prior nudges here
_QUANT_CONF_MIN = 0.5  # below this we emit raw onset times (don't trust tempo)


def transcribe_drums(wav_path: Path, tempo: float | None = None) -> Transcription:
    y, sr = librosa.load(str(wav_path), sr=_SR, mono=True)

    # Detect + classify every onset first; the kept (non-DROP) onsets are the
    # signal the tempo/phase estimator fits — noise onsets would pollute it.
    kept: list[tuple[float, int, int]] = []  # (time_s, midi_note, velocity)
    for t in _detect_onsets(y, sr):
        features = _features_at(y, sr, t)
        note = _classify(features)
        if note == DROP:
            continue
        kept.append((float(t), note, _velocity_from_rms(features["rms"])))

    kept_times = np.array([t for t, _, _ in kept], dtype=float)

    if tempo is not None and tempo > 0:
        tempo_bpm, confidence = float(tempo), 1.0  # explicit tempo is trusted as-is
    else:
        tempo_bpm, confidence = _detect_tempo(y, sr, kept_times)

    # Quantise only when the tempo is trustworthy. Snapping to a *wrong* tempo
    # (or wrong grid phase) shears every hit off the played timing and tanks
    # onset F1; raw onsets are the safe fallback. See _quantise_grid.
    trust_tempo = confidence >= _QUANT_CONF_MIN
    if trust_tempo:
        if tempo is None:
            # Sharpen the octave-correct estimate to sub-BPM: even ~0.5 BPM of
            # error drifts the grid past the match tolerance late in a clip.
            tempo_bpm = _refine_tempo(kept_times, tempo_bpm)
        phase = _grid_phase(kept_times, tempo_bpm)

    hits: list[DrumHit] = []
    seen: set[tuple[int, int]] = set()
    for t, note, velocity in kept:
        if trust_tempo:
            t_out = _quantise_grid(t, tempo_bpm, phase)
            # Two onsets that collapse onto one grid slot + pitch are one hit.
            key = (round(t_out * 1000), note)
            if key in seen:
                continue
            seen.add(key)
        else:
            t_out = t
        hits.append(DrumHit(time_s=t_out, midi_note=note, velocity=velocity))

    bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

    midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
    _write_midi(midi_path, hits, tempo_bpm)

    return Transcription(
        midi_path=midi_path,
        tempo_bpm=float(tempo_bpm),
        bars=float(bars),
        hits=hits,
    )


# --- stages ---


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


def _detect_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    frames = librosa.onset.onset_detect(
        y=y,
        sr=sr,
        backtrack=True,
        units="frames",
        delta=_ONSET_DELTA,
        wait=max(1, int(_ONSET_WAIT_S * sr / 512)),  # 512 = librosa default hop
    )
    return librosa.frames_to_time(frames, sr=sr)


def _features_at(y: np.ndarray, sr: int, t: float) -> dict[str, float]:
    start = int(t * sr)
    end = min(start + int(_WINDOW_S * sr), len(y))
    frame = y[start:end]
    if len(frame) < 64:
        return {
            "centroid": 0.0,
            "flatness": 0.0,
            "zcr": 0.0,
            "rms": 0.0,
            "sub100_ratio": 0.0,
            "decay_s": 0.0,
        }

    # n_fft capped to frame length (librosa warns otherwise).
    n_fft = min(1024, 1 << (len(frame) - 1).bit_length())

    centroid = float(librosa.feature.spectral_centroid(y=frame, sr=sr, n_fft=n_fft).mean())
    flatness = float(librosa.feature.spectral_flatness(y=frame, n_fft=n_fft).mean())
    zcr = float(librosa.feature.zero_crossing_rate(y=frame).mean())
    rms = float(np.sqrt(np.mean(frame**2)))

    spec = np.abs(np.fft.rfft(frame, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, d=1 / sr)
    total = spec.sum() + 1e-9
    sub100_ratio = float(spec[freqs < 100].sum() / total)

    # Decay: time from peak RMS to -12dB, computed in 10ms hops.
    hop = max(1, int(0.010 * sr))
    rms_env = np.array([np.sqrt(np.mean(frame[i : i + hop] ** 2)) for i in range(0, len(frame) - hop, hop)])
    if rms_env.size > 1 and rms_env.max() > 0:
        peak = rms_env.argmax()
        threshold = rms_env.max() * 0.25  # -12 dB
        tail = rms_env[peak:]
        below = np.where(tail < threshold)[0]
        decay_s = (below[0] if below.size else len(tail)) * hop / sr
    else:
        decay_s = 0.0

    return {
        "centroid": centroid,
        "flatness": flatness,
        "zcr": zcr,
        "rms": rms,
        "sub100_ratio": sub100_ratio,
        "decay_s": decay_s,
    }


_MODEL_PATH = Path(__file__).resolve().parent / "drum_model.json"


def _load_model() -> dict | None:
    """Load the per-user trained model, or None if absent/invalid."""
    try:
        return json.loads(_MODEL_PATH.read_text())
    except (OSError, ValueError):
        return None


_MODEL = _load_model()


def _classify(f: dict[str, float]) -> int:
    """Classify one onset to a GM pitch (or DROP).

    Uses the per-user trained model (``drum_model.json``) when present:
    loudness gates silence, then the standardised timbre features are matched
    to the model. Supports a k-NN model (exemplar vote — handles multi-modal
    classes like fast vs slow hats) or a nearest-centroid model. Falls back to
    the hand-tuned heuristic when no model is available.
    """
    if _MODEL is None:
        return _classify_heuristic(f)
    if f["rms"] < _MODEL.get("rms_floor", 0.005):
        return DROP
    mean, std, feats = _MODEL["mean"], _MODEL["std"], _MODEL["features"]
    z = [(f[k] - mean[j]) / std[j] for j, k in enumerate(feats)]
    if _MODEL.get("type") == "knn":
        ex, labels, k = _MODEL["exemplars"], _MODEL["labels"], _MODEL.get("k", 5)
        order = sorted(
            range(len(ex)),
            key=lambda i: sum((ex[i][j] - z[j]) ** 2 for j in range(len(z))),
        )
        from collections import Counter

        label = Counter(labels[i] for i in order[:k]).most_common(1)[0][0]
        return int(_MODEL["classes"][label])
    # nearest-centroid
    best_label, best_d = None, float("inf")
    for label, c in _MODEL["centroids"].items():
        d = sum((z[j] - c[j]) ** 2 for j in range(len(z)))
        if d < best_d:
            best_label, best_d = label, d
    return int(_MODEL["classes"][best_label])


def _classify_heuristic(f: dict[str, float]) -> int:
    """Hand-tuned fallback classifier. Returns a GM pitch or DROP.

    Ordering: kick (sub-bass dominant) > hat (very high centroid) > snare
    (mid band) > drop.
    """
    centroid = f["centroid"]
    sub100 = f["sub100_ratio"]
    decay = f["decay_s"]
    rms = f["rms"]

    if rms < 0.01:
        return DROP

    if sub100 > 0.25 or (centroid < 1200 and sub100 > 0.10):
        return GM_KICK
    if centroid > 5000:
        return GM_HAT_OPEN if decay > 0.060 else GM_HAT_CLOSED
    if 1200 <= centroid <= 5000:
        return GM_SNARE
    return DROP


def _velocity_from_rms(rms: float) -> int:
    # Map rms ∈ [0.01, 0.3] logarithmically to [40, 120], clamp.
    if rms <= 0:
        return 40
    db = 20 * np.log10(max(rms, 1e-4))
    # -40 dB -> 40, -10 dB -> 120.
    vel = 40 + (db - (-40)) * (120 - 40) / 30
    return int(np.clip(vel, 1, 127))


def _quantise_16th(t_s: float, tempo_bpm: float) -> float:
    step = 60.0 / tempo_bpm / 4.0
    return round(t_s / step) * step


def _quantise_grid(t_s: float, tempo_bpm: float, phase: float) -> float:
    """Snap to a 16th grid whose lines sit at ``(n + phase) * step``.

    ``phase`` (from :func:`_grid_phase`) aligns the grid to the performer's
    lead-in so snapping pulls hits toward the played timing rather than an
    arbitrary phase-0 grid. ``phase=0`` reduces to :func:`_quantise_16th`.
    """
    step = 60.0 / tempo_bpm / 4.0
    return (round(t_s / step - phase) + phase) * step


def _write_midi(path: Path, hits: list[DrumHit], tempo_bpm: float) -> None:
    tpb = 480
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))

    events: list[tuple[int, str, int, int]] = []
    for hit in hits:
        tick = int(round(hit.time_s * tempo_bpm / 60.0 * tpb))
        events.append((tick, "on", hit.midi_note, hit.velocity))
        events.append((tick + tpb // 8, "off", hit.midi_note, 0))  # 1/32-note duration
    events.sort()

    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta, channel=9))

    mid.save(path)
