"""Drum classifier tiers: per-user k-NN -> global MLP -> heuristic, and feature-set dispatch."""

from __future__ import annotations

import json

import numpy as np
import pytest

from mouthflow import signal
from mouthflow.devices.drum import classify as drum_classify
from mouthflow.devices.drum import features as F
from mouthflow.devices.drum.classify import GM_HAT_CLOSED, GM_KICK, GM_SNARE, _classify

SR = signal._SR


def _click(seed=0):
    rng = np.random.default_rng(seed)
    y = np.zeros(SR // 2, dtype=np.float32)
    y[:1200] = (rng.normal(0, 0.3, 1200) * np.exp(-np.arange(1200) / 300)).astype(np.float32)
    return y


def test_feature_sets_have_matching_names_and_dims():
    y = _click()
    for name, (names, fn) in F.FEATURE_SETS.items():
        vec = fn(y, SR, 0.0)
        assert len(vec) == len(names), name
        assert np.all(np.isfinite(vec)), name
    assert F.FEATURE_SETS["all123"][1](y, SR, 0.0)[:10] == F.FEATURE_SETS["drum10"][1](y, SR, 0.0)


def test_logmel_is_level_invariant():
    y = _click()
    a = np.array(F.logmel3(y, SR, 0.0))
    b = np.array(F.logmel3(y * 0.1, SR, 0.0))
    c = np.array(F.logmel3(y * 3.0, SR, 0.0))
    # each slice is normalised by its own energy and the floor is relative to
    # the hit's peak, so mic gain cancels (incl. the decay slopes)
    assert np.allclose(a, b, atol=1e-3) and np.allclose(a, c, atol=1e-3)


def _global_model(feature_set: str, winner: str) -> dict:
    """A synthetic global MLP whose output layer always favours ``winner``."""
    d = len(F.FEATURE_SETS[feature_set][0])
    classes = ["kick", "snare", "hat"]
    W2 = np.zeros((4, 3))
    W2[:, classes.index(winner)] = 1.0
    return {
        "type": "mlp",
        "feature_set": feature_set,
        "classes": classes,
        "pitches": {"kick": GM_KICK, "snare": GM_SNARE, "hat": GM_HAT_CLOSED},
        "mean": [0.0] * d,
        "std": [1.0] * d,
        "W1": np.zeros((d, 4)).tolist(),
        "b1": [1.0] * 4,  # hidden activations = 1 -> logits = W2 column sums
        "W2": W2.tolist(),
        "b2": [0.0, 0.0, 0.0],
        "rms_floor": 0.005,
    }


def test_global_tier_used_when_no_user_model(monkeypatch):
    monkeypatch.setattr(drum_classify, "_MODEL", None)
    monkeypatch.setattr(drum_classify, "_GLOBAL", _global_model("all123", "snare"))
    y = _click()
    assert _classify(y, SR, 0.0, signal.features_at(y, SR, 0.0)) == GM_SNARE


def test_user_model_beats_global(monkeypatch):
    d = len(F.FEATURES)
    user = {
        "type": "knn",
        "k": 1,
        "feature_set": "drum10",
        "mean": [0.0] * d,
        "std": [1.0] * d,
        "classes": {"kick": GM_KICK},
        "exemplars": [[0.0] * d],
        "labels": ["kick"],
        "rms_floor": 0.005,
    }
    monkeypatch.setattr(drum_classify, "_MODEL", user)
    monkeypatch.setattr(drum_classify, "_GLOBAL", _global_model("all123", "snare"))
    y = _click()
    assert _classify(y, SR, 0.0, signal.features_at(y, SR, 0.0)) == GM_KICK


def test_legacy_user_model_without_feature_set_uses_drum10(monkeypatch):
    d = len(F.FEATURES)
    legacy = {
        "type": "knn",
        "k": 1,
        "mean": [0.0] * d,
        "std": [1.0] * d,
        "classes": {"hat": GM_HAT_CLOSED},
        "exemplars": [[0.0] * d],
        "labels": ["hat"],
    }
    monkeypatch.setattr(drum_classify, "_MODEL", legacy)
    y = _click()
    assert _classify(y, SR, 0.0, signal.features_at(y, SR, 0.0)) == GM_HAT_CLOSED


def test_silence_drops_before_any_model(monkeypatch):
    monkeypatch.setattr(drum_classify, "_MODEL", None)
    monkeypatch.setattr(drum_classify, "_GLOBAL", _global_model("all123", "snare"))
    y = np.zeros(SR // 2, dtype=np.float32)
    assert _classify(y, SR, 0.0, signal.features_at(y, SR, 0.0)) == drum_classify.DROP


def test_shipped_model_files_are_consistent():
    """The committed models must declare a known feature set with matching dims."""
    for path in (drum_classify._MODEL_PATH, drum_classify._GLOBAL_PATH):
        if not path.exists():
            pytest.skip(f"{path.name} not present")
        m = json.loads(path.read_text())
        fs = m.get("feature_set", "drum10")
        assert fs in F.FEATURE_SETS
        assert len(m["mean"]) == len(F.FEATURE_SETS[fs][0])
        if m["type"] == "mlp":
            assert np.asarray(m["W1"]).shape == (len(m["mean"]), m["hidden"])
            assert set(m["classes"]) == set(m["pitches"])
            assert m["source"]["license"] == "CC BY 4.0"


def test_noise_needs_a_supermajority(monkeypatch):
    """3 of 5 nearest say noise, 2 say kick -> kick (noise_min_votes 4); 4 of 5 -> DROP."""
    d = len(F.FEATURES)
    y = _click()
    base = np.array(F.drum_features(y, SR, 0.0))

    def model(n_noise_near):
        ex = [base.tolist()] * 5  # five equidistant exemplars; label mix decides
        labels = ["noise"] * n_noise_near + ["kick"] * (5 - n_noise_near)
        return {"type": "knn", "k": 5, "feature_set": "drum10", "mean": [0.0] * d, "std": [1.0] * d,
                "classes": {"kick": GM_KICK, "noise": drum_classify.DROP}, "exemplars": ex,
                "labels": labels, "rms_floor": 0.005, "noise_min_votes": 4}

    f = signal.features_at(y, SR, 0.0)
    monkeypatch.setattr(drum_classify, "_MODEL", model(3))
    assert _classify(y, SR, 0.0, f) == GM_KICK
    monkeypatch.setattr(drum_classify, "_MODEL", model(4))
    assert _classify(y, SR, 0.0, f) == drum_classify.DROP
