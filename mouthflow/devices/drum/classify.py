"""Drum-specific per-onset classifier: per-user k-NN -> global MLP -> heuristic.

Three tiers, best available wins:

1. ``drum_model.json`` — the **per-user** k-NN trained on this performer's own
   enrollment hits: ``~/.mouthflow/drum_model.json`` from ``mouthflow enroll``
   first, else the packaged one (``eval/train_classifier``). 83 % on AVP
   improvisations after a ~30-hits-per-class enrollment; this is the product.
2. ``drum_global.json`` — a small MLP trained on the AVP cross-performer
   dataset (``eval/train_global``; CC BY 4.0, 25 amateurs on a MacBook mic).
   68.8 % leave-one-performer-out: what a fresh install gets before enrolling.
3. The hand-tuned heuristic — 41.7 % on other voices; kept only as the
   last resort when neither model file is present.

Both model files load **lazily** at first use, not at import, so importing
the device registry costs nothing for the bass/lead/drone paths. ``_classify``
reads ``_MODEL`` / ``_GLOBAL`` through the module globals at call time, so a
test can ``monkeypatch.setattr(this_module, "_MODEL", None)`` (and
``"_GLOBAL"``) to force a lower tier.
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

# Model files. The per-user k-NN is looked up in the user's state dir first
# (written by `mouthflow enroll train`), then the packaged one (the dev
# performer's enrollment, written by eval/train_classifier); the global MLP
# ships in the package (eval/train_global).
_PKG = Path(__file__).resolve().parents[2]
_USER_MODEL_PATH = Path.home() / ".mouthflow" / "drum_model.json"
_MODEL_PATH = _PKG / "drum_model.json"
_GLOBAL_PATH = _PKG / "drum_global.json"

_UNSET = object()
_MODEL = _UNSET  # lazily populated by _get_model() on first classify()
_GLOBAL = _UNSET  # lazily populated by _get_global() on first classify()


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _load_model() -> dict | None:
    """Load the per-user model: the enrolled one if present, else the packaged one."""
    for path in (_USER_MODEL_PATH, _MODEL_PATH):
        m = _load_json(path)
        if m:
            return m
    return None


def _get_model() -> dict | None:
    global _MODEL
    if _MODEL is _UNSET:
        _MODEL = _load_model()
    return _MODEL


def _get_global() -> dict | None:
    global _GLOBAL
    if _GLOBAL is _UNSET:
        _GLOBAL = _load_json(_GLOBAL_PATH)
    return _GLOBAL


def _knn_predict(model: dict, audio, sr: int, t: float) -> int:
    """Per-user k-NN: standardise the onset's feature vector, vote k nearest."""
    from mouthflow.devices.drum.features import extract

    vec = extract(model.get("feature_set", "drum10"), audio, sr, t)
    mean, std = model["mean"], model["std"]
    z = [(vec[j] - mean[j]) / std[j] for j in range(len(vec))]
    ex, labels, k = model["exemplars"], model["labels"], model.get("k", 5)
    order = sorted(
        range(len(ex)),
        key=lambda i: sum((ex[i][j] - z[j]) ** 2 for j in range(len(z))),
    )
    label = Counter(labels[i] for i in order[:k]).most_common(1)[0][0]
    return int(model["classes"][label])


def _mlp_predict(g: dict, audio, sr: int, t: float) -> int:
    """Global MLP (one ReLU hidden layer) forward pass — numpy only."""
    import numpy as np

    from mouthflow.devices.drum.features import extract

    vec = np.asarray(extract(g["feature_set"], audio, sr, t), dtype=float)
    z = (vec - np.asarray(g["mean"])) / np.asarray(g["std"])
    h = np.maximum(z @ np.asarray(g["W1"]) + np.asarray(g["b1"]), 0.0)
    logits = h @ np.asarray(g["W2"]) + np.asarray(g["b2"])
    label = g["classes"][int(np.argmax(logits))]
    return int(g["pitches"][label])


def _classify(audio, sr: int, t: float, f: dict[str, float]) -> int:
    """Classify the onset at time ``t`` to a GM pitch (or DROP).

    ``f["rms"]`` gates silence for both model tiers. The per-user k-NN wins
    when present (vote of the k nearest exemplars — handles multi-modal
    classes like fast vs slow hats); otherwise the cross-performer MLP;
    otherwise the hand-tuned heuristic (which reads ``f``).
    """
    model = _get_model()
    if model is not None:
        if f["rms"] < model.get("rms_floor", 0.005):
            return DROP
        return _knn_predict(model, audio, sr, t)
    g = _get_global()
    if g is not None:
        if f["rms"] < g.get("rms_floor", 0.005):
            return DROP
        return _mlp_predict(g, audio, sr, t)
    return _classify_heuristic(f)


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
