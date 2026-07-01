"""Drum-specific per-onset classifier (GM note map + k-NN model).

This is the one genuinely drum-specific piece of the old ``transcribe.py``.
The trained model (``drum_model.json``) is loaded **lazily** at first use, not
at import, so importing the device registry (which pulls in this module) costs
nothing for the bass/lead/drone paths that don't need it.

``_classify`` reads ``_MODEL`` through the module global at call time, so a test
can ``monkeypatch.setattr(this_module, "_MODEL", None)`` to force the heuristic.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

GM_KICK = 36
GM_SNARE = 38
GM_HAT_CLOSED = 42
GM_HAT_OPEN = 46
GM_PERC = 39  # reserved

DROP = -1  # sentinel: silence rather than guess

# drum_model.json stays at the package root (train_classifier.py writes it there)
_MODEL_PATH = Path(__file__).resolve().parents[2] / "drum_model.json"

_UNSET = object()
_MODEL = _UNSET  # lazily populated by _get_model() on first classify()


def _load_model() -> dict | None:
    """Load the per-user trained model, or None if absent/invalid."""
    try:
        return json.loads(_MODEL_PATH.read_text())
    except (OSError, ValueError):
        return None


def _get_model() -> dict | None:
    global _MODEL
    if _MODEL is _UNSET:
        _MODEL = _load_model()
    return _MODEL


def _classify(audio, sr: int, t: float, f: dict[str, float]) -> int:
    """Classify the onset at time ``t`` to a GM pitch (or DROP).

    Uses the per-user k-NN model when present: ``f["rms"]`` gates silence, then
    the 10-feature ``drum_features`` vector is standardised and matched to the
    exemplars (vote of the k nearest — handles multi-modal classes like fast vs
    slow hats). Falls back to the hand-tuned heuristic (which reads ``f``) when
    no model is available.
    """
    model = _get_model()
    if model is None:
        return _classify_heuristic(f)
    if f["rms"] < model.get("rms_floor", 0.005):
        return DROP

    from mouthflow.devices.drum.features import drum_features

    vec = drum_features(audio, sr, t)
    mean, std = model["mean"], model["std"]
    z = [(vec[j] - mean[j]) / std[j] for j in range(len(vec))]
    ex, labels, k = model["exemplars"], model["labels"], model.get("k", 5)
    order = sorted(
        range(len(ex)),
        key=lambda i: sum((ex[i][j] - z[j]) ** 2 for j in range(len(z))),
    )
    label = Counter(labels[i] for i in order[:k]).most_common(1)[0][0]
    return int(model["classes"][label])


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
