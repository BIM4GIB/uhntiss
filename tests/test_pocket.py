"""Pocket bridge: metric replica, medoid picking, profile layout, enroll export."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from mouthflow import enroll, pocket
from mouthflow.devices.drum import classify as drum_classify


def test_model_scale_parses_the_normal_rate_namespace():
    if not pocket.HEADER.exists():
        pytest.skip("native/pocket header not present")
    s = pocket.model_scale()
    assert s.shape == (17,) and np.all(s > 0)
    # the first array in the header is pocket::model (the engine's Neighbours metric)
    assert s[0] == pytest.approx(2.2998814621038037)


def test_vote_replicates_neighbours_rule():
    scale = np.ones(17)
    ex = [(np.zeros(17), 0), (np.full(17, 1.0), 1), (np.full(17, 2.0), 2), (np.full(17, 0.05), 2)]
    # nearest three to a point at 0.1: kick(0), hat(0.05), snare(1.0); weights 1/(1+d)
    f = np.full(17, 0.1)
    d = [pocket.distance(f, e, scale) for e, _ in ex]
    assert d[3] < d[0] < d[1] < d[2]
    # hat(0.05) wins over kick: closer -> larger weight
    assert pocket.vote(f, ex, scale) == 2
    # with k=1 the single nearest decides
    assert pocket.vote(f, ex, scale, k=1) == 2
    # tie-break: equal distances keep the earlier example
    tie = [(np.zeros(17), 1), (np.zeros(17), 0), (np.zeros(17), 0)]
    assert pocket.vote(np.zeros(17), tie, scale, k=1) == 1


def test_pick_examples_takes_medoids_and_requires_three():
    rng = np.random.default_rng(0)
    scale = np.ones(17)
    cloud = rng.normal(0, 1, (20, 17))
    cloud[7] = 0.0  # the exact centroid-ish point must be picked first
    picked = pocket.pick_examples({0: cloud, 1: cloud + 10, 2: cloud - 10}, scale)
    assert picked[0].shape == (3, 17)
    assert np.allclose(picked[0][0], 0.0)
    with pytest.raises(pocket.PocketError, match="snare: only 2"):
        pocket.pick_examples({0: cloud, 1: cloud[:2], 2: cloud}, scale)


def test_profile_values_layout_matches_engine():
    ex = {d: np.full((3, 17), float(d)) for d in range(3)}
    vals = pocket.profile_values(ex)
    assert len(vals) == pocket.PROFILE_VALUES == 156
    assert vals[0] == 3 and vals[52] == 3 and vals[104] == 3  # counts at each drum block start
    assert vals[1:52] == [0.0] * 51 and vals[53:104] == [1.0] * 51
    with pytest.raises(pocket.PocketError):
        pocket.profile_values({0: np.full((3, 17), 100.0), 1: ex[1], 2: ex[2]})


def test_write_profile_json(tmp_path, monkeypatch):
    lib = tmp_path / "Documents" / "Max 9" / "Library"
    lib.mkdir(parents=True)
    monkeypatch.setattr(pocket, "max_library_dirs", lambda: [lib])
    ex = {d: np.full((3, 17), 0.5 * d) for d in range(3)}
    doc = pocket.write_profile(ex, {"sample_rate": 48000}, path=tmp_path / "p.json")
    assert (tmp_path / "p.json").exists() and (lib / pocket.MAX_PROFILE_NAME).exists()
    saved = json.loads((lib / pocket.MAX_PROFILE_NAME).read_text())
    assert saved["version"] == 4 and len(saved["values"]) == 156 and saved["sample_rate"] == 48000
    assert len(doc["written"]) == 2


def _fake_analyze(drum_of_path):
    """Stand-in for pocket_analyze: 10 detections per clip, features centred per class."""

    def fake(binary, samples, sr, gate=0.010, policy=None):
        rng = np.random.default_rng(int(abs(samples[:100].sum() * 1e6)) % 2**32)
        drum = drum_of_path["current"]
        rows = []
        for i in range(10):
            feats = rng.normal(0, 0.05, 17) + drum * 2.0
            rows.append([0.0, drum, 0.6, 0.2 + i * 0.5, *feats])
        return np.array(rows)

    return fake


def test_export_pocket_builds_profile_from_enrollment(tmp_path, monkeypatch):
    import soundfile as sf

    monkeypatch.setattr(pocket, "find_binary", lambda explicit=None: tmp_path / "fake")
    monkeypatch.setattr(pocket, "model_scale", lambda header=None: np.ones(17))
    current = {"current": 0}
    monkeypatch.setattr(pocket, "analyze", _fake_analyze(current))
    # enroll three clips (content irrelevant to the fake analyzer; must contain hits for add_clip)
    sr = enroll.SR
    for cls, drum in (("kick", 0), ("snare", 1), ("hat", 2)):
        y = np.zeros(sr * 2, dtype=np.float32)
        rng = np.random.default_rng(drum)
        for i in range(4):
            s0 = int((0.2 + i * 0.4) * sr)
            y[s0 : s0 + 1500] = (rng.normal(0, 0.4, 1500) * np.exp(-np.arange(1500) / 300)).astype(np.float32)
            y[s0 + 1100 : s0 + 1500] *= np.linspace(1, 0, 400)
        p = tmp_path / f"{cls}.wav"
        sf.write(p, y, sr, subtype="PCM_16")
        enroll.add_clip(cls, p)
    # export: the fake analyzer labels by the clip currently being analysed
    orig_load = enroll._load_audio

    def load_and_tag(path):
        current["current"] = {"kick": 0, "snare": 1, "hat": 2}[path.name.split("-")[0]]
        return orig_load(path)

    monkeypatch.setattr(enroll, "_load_audio", load_and_tag)
    rep = enroll.export_pocket(sr=48000)
    assert rep["hits_per_drum"] == {"kick": 10, "snare": 10, "hat": 10}
    doc = json.loads(pocket.profile_path().read_text())  # isolated by conftest
    assert doc["version"] == 4 and len(doc["values"]) == 156 and doc["pick"] == "medoid"
    # drum blocks carry the class-centred features
    assert abs(np.mean(doc["values"][1:52]) - 0.0) < 0.1
    assert abs(np.mean(doc["values"][53:104]) - 2.0) < 0.1
    assert abs(np.mean(doc["values"][105:156]) - 4.0) < 0.1


@pytest.mark.skipif(not pocket.BINARY.exists(), reason="pocket_analyze not built")
def test_real_binary_detects_clicks_and_emits_17_features():
    sr = 48000
    y = np.zeros(sr * 2, dtype=np.float32)
    rng = np.random.default_rng(3)
    for i in range(4):
        s0 = int((0.3 + i * 0.4) * sr)
        y[s0 : s0 + 2000] = (rng.normal(0, 0.5, 2000) * np.exp(-np.arange(2000) / 400)).astype(np.float32)
    rows = pocket.analyze(pocket.BINARY, y, sr)
    assert rows.shape[1] == 4 + 17
    acc = pocket.accepted(rows)
    assert 1 <= len(acc) <= 8
    assert np.all(np.isfinite(acc[:, 4:]))
