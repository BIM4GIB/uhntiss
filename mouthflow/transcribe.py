"""Beatbox WAV -> drum MIDI + tempo.

This module is now a thin **drum device** layer over the shared DSP in
``mouthflow.signal``. The generic pieces (onset/tempo detection, feature
extraction, quantization, MIDI writing) live in ``signal.py``; what remains
here is drum-specific: the GM note map and the per-onset classifier
(``_classify`` + the k-NN ``drum_model.json``, with ``_classify_heuristic`` as
the always-present fallback).

The historic public names (``_SR``, ``_detect_onsets``, ``_features_at``,
``_quantise_16th``, ``_write_midi`` …) are re-exported below so existing
callers — ``eval/run_eval.py``, ``eval/train_classifier.py``, ``mimic/take.py``
and the tests — keep importing them from here unchanged.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mouthflow import signal
from mouthflow.schemas import DrumHit, Transcription

GM_KICK = 36
GM_SNARE = 38
GM_HAT_CLOSED = 42
GM_HAT_OPEN = 46
GM_PERC = 39  # unused in v0.1 but reserved

DROP = -1  # sentinel returned by classify when we'd rather silence than guess

# --- re-exports of the shared DSP (back-compat for eval/, mimic/, tests) ---
_SR = signal._SR
_WINDOW_S = signal._WINDOW_S
_detect_tempo = signal.detect_tempo
_detect_onsets = signal.detect_onsets
_features_at = signal.features_at
_velocity_from_rms = signal.velocity_from_rms


def _quantise_16th(t_s: float, tempo_bpm: float) -> float:
    """Back-compat alias: snap to 16th notes via ``signal.quantise``."""
    return signal.quantise(t_s, tempo_bpm, division=16)


def _write_midi(path: Path, hits: list[DrumHit], tempo_bpm: float) -> None:
    """Back-compat alias: GM drum write (channel 9, 1/32-note durations)."""
    signal.write_midi(path, hits, tempo_bpm, channel=9)


def transcribe_drums(wav_path: Path) -> Transcription:
    import librosa

    y, sr = librosa.load(str(wav_path), sr=_SR, mono=True)

    tempo_bpm = signal.detect_tempo(y, sr)
    onset_times = signal.detect_onsets(y, sr)

    hits: list[DrumHit] = []
    for t in onset_times:
        features = signal.features_at(y, sr, t)
        note = _classify(features)
        if note == DROP:
            continue
        velocity = signal.velocity_from_rms(features["rms"])
        t_quantised = signal.quantise(t, tempo_bpm, division=16)
        hits.append(DrumHit(time_s=t_quantised, midi_note=note, velocity=velocity))

    bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

    midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
    signal.write_midi(midi_path, hits, tempo_bpm, channel=9)

    return Transcription(
        midi_path=midi_path,
        tempo_bpm=float(tempo_bpm),
        bars=float(bars),
        hits=hits,
    )


# --- drum classifier (drum-specific; moves to devices/drum/ in a later step) ---

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
