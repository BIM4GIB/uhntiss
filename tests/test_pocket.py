"""Pocket bridge: metric replica, medoid picking, profile layout, enroll export."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from mouthflow import enroll, pocket


def test_model_scale_parses_the_normal_rate_namespace():
    if not pocket.HEADER.exists():
        pytest.skip("native/pocket header not present")
    s = pocket.model_scale()
    assert s.shape == (17,) and np.all(s > 0)
    # the first array in the header is pocket::model (the engine's Neighbours metric)
    assert s[0] == pytest.approx(2.2998814621038037)


def test_vote_replicates_neighbours_rule():
    scale = np.ones(17)
    ex = [
        (np.zeros(17), 0),
        (np.full(17, 1.0), 1),
        (np.full(17, 2.0), 2),
        (np.full(17, 0.05), 2),
    ]
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
    assert (
        vals[0] == 3 and vals[52] == 3 and vals[104] == 3
    )  # counts at each drum block start
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
    assert (
        saved["version"] == 4
        and len(saved["values"]) == 156
        and saved["sample_rate"] == 48000
    )
    assert len(doc["written"]) == 2


def _fake_analyze(drum_of_path):
    """Stand-in for pocket_analyze: 10 detections per clip, features centred per class."""

    def fake(binary, samples, sr, gate=0.010, policy=None):
        rng = np.random.default_rng(int(abs(samples[:100].sum() * 1e6)) % 2**32)
        drum = drum_of_path["current"]
        rows = []
        for i in range(10):
            feats = rng.normal(0, 0.05, 17) + drum * 2.0
            rows.append(
                [
                    0.2 + i * 0.5,
                    drum,
                    0.6,
                    0.21 + i * 0.5,
                    *feats,
                    *rng.normal(drum * 2.0, 0.05, 120),
                    480,
                    1440,
                    1920,
                    0.28 + i * 0.5,
                ]
            )
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
            y[s0 : s0 + 1500] = (
                rng.normal(0, 0.4, 1500) * np.exp(-np.arange(1500) / 300)
            ).astype(np.float32)
            y[s0 + 1100 : s0 + 1500] *= np.linspace(1, 0, 400)
        p = tmp_path / f"{cls}.wav"
        sf.write(p, y, sr, subtype="PCM_16")
        enroll.add_clip(cls, p)
    # export: the fake analyzer labels by the clip currently being analysed
    orig_load = enroll._load_audio

    def load_and_tag(path):
        current["current"] = {
            "kick": 0,
            "snare": 1,
            "hat": 2,
            "rimshot": 1,
            "noise": 2,
        }[path.stem.split("-")[0]]
        return orig_load(path)

    monkeypatch.setattr(enroll, "_load_audio", load_and_tag)
    enroll.add_clip("rimshot", tmp_path / "snare.wav", note=37)  # a custom mapped sound
    enroll.add_clip(
        "noise", tmp_path / "hat.wav"
    )  # the voice clip: not a Pocket voice, not "skipped" either
    rep = enroll.export_pocket(sr=48000)
    assert rep["hits_per_drum"] == {"kick": 10, "snare": 10, "hat": 10, "rimshot": 10}
    assert not rep["skipped"]
    doc = json.loads(pocket.profile_path().read_text())  # isolated by conftest
    assert doc["version"] == 5 and doc["feature_version"] == pocket.FEATURE_VERSION == 3
    assert [v["name"] for v in doc["voices"]] == ["kick", "snare", "hat", "rimshot"]
    assert doc["voices"][-1]["note"] == 37
    assert doc["reject_examples"] == 10
    assert len(doc["values"]) == 246 + 4 * pocket.VOICE_VALUES + 10 * (17 + 31)


@pytest.mark.skipif(not pocket.BINARY.exists(), reason="pocket_analyze not built")
def test_real_binary_detects_clicks_and_emits_17_features():
    sr = 48000
    y = np.zeros(sr * 2, dtype=np.float32)
    rng = np.random.default_rng(3)
    for i in range(4):
        s0 = int((0.3 + i * 0.4) * sr)
        y[s0 : s0 + 2000] = (
            rng.normal(0, 0.5, 2000) * np.exp(-np.arange(2000) / 400)
        ).astype(np.float32)
    rows = pocket.analyze(pocket.BINARY, y, sr)
    assert rows.shape[1] == 4 + 17
    acc = pocket.accepted(rows)
    assert 1 <= len(acc) <= 8
    assert np.all(np.isfinite(acc[:, 4:]))


def enrollment_rows(d, n=30, sr=48000):
    rng = np.random.default_rng(901 + d)
    x = np.zeros((n, 145))
    x[:, 0] = 0.2 + np.arange(n) * 0.4
    x[:, 1] = 3  # Factory rejects: enrollment still owns the labels.
    x[:, 2] = 0.5
    x[:, 3] = x[:, 0] + 0.01 - 1 / sr
    x[:, 4:21] = rng.normal(d * 4, 0.1, (n, 17))
    x[:, 21:141] = rng.normal(d * 3, 0.1, (n, 120))
    x[:, 141:144] = np.round(np.array([0.01, 0.03, 0.04]) * sr)
    x[:, 144] = x[:, 0] + 0.08
    return x


def test_v5_keeps_all_named_voices_and_enrollment_bank():
    voices = [
        {"name": f"voice_{d}", "note": d + 36, "rows": enrollment_rows(d, 30)}
        for d in range(4)
    ]
    doc = pocket.build_profile(voices, 48000)
    assert len(doc["values"]) == 246 + 4 * pocket.VOICE_VALUES
    assert doc["values"][246 + 25 : 246 + 27] == [30, 30]
    for d, voice in enumerate(voices):
        pred = pocket.predict_profile(doc, voice["rows"])
        assert np.all(pred == d)
    # An explicit negative is never turned into a delayed loop hit.
    row = voices[3]["rows"][0].copy()
    row[4:21] += 0.15
    rejected = pocket.build_profile(voices, 48000, np.tile(row[4:21], (4, 1)))
    assert pocket.predict_profile(rejected, row.reshape(1, -1)).tolist() == [[-1, -1]]
    # Building the profile cannot inspect, mutate or standardize query rows.
    query = enrollment_rows(2)
    before = json.dumps(doc, sort_keys=True)
    pocket.predict_profile(doc, query * 1.1)
    assert json.dumps(doc, sort_keys=True) == before


def test_v5_rejects_bad_enrollment_before_writing():
    voices = [
        {"name": f"voice_{d}", "note": 36 + d, "rows": enrollment_rows(d)}
        for d in range(2)
    ]
    voices[0]["rows"][0, 7] = np.nan
    with pytest.raises(pocket.PocketError, match="finite"):
        pocket.build_profile(voices, 48000)
    voices[0]["rows"] = enrollment_rows(0)
    voices[0]["name"] = "bad name"
    with pytest.raises(pocket.PocketError, match="names"):
        pocket.build_profile(voices, 48000)


def test_atomic_profile_write_leaves_no_partial_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(pocket, "max_library_dirs", lambda: [])
    path = tmp_path / "profile.json"
    path.write_text('{"original": true}')
    with pytest.raises(ValueError):
        pocket.write_profile_document({"bad": float("nan")}, path)
    assert json.loads(path.read_text()) == {"original": True}


@pytest.mark.skipif(not pocket.BINARY.exists(), reason="native core not built")
def test_native_slices_match_independent_reference_without_future_audio():
    from eval.avp_window import window_features

    sr = 48000
    y = np.zeros(sr, dtype=np.float32)
    t = np.arange(3000) / sr
    y[9600:12600] = 0.6 * np.sin(2 * np.pi * 110 * t) * np.exp(-40 * t)
    rows = pocket.analyze(pocket.BINARY, y, sr, policy="enrollment")
    assert len(rows) >= 1
    row = rows[0]
    for part, offset in enumerate((0, 0.01, 0.04)):
        length = int(row[141 + part])
        assert length >= 32
        reference = window_features(
            y, sr, row[0] + offset, length / sr * 1000, pre_ms=30 if part == 0 else 0
        )
        np.testing.assert_allclose(
            row[21 + 40 * part : 61 + 40 * part], reference, atol=2e-6, rtol=1e-7
        )
    changed = y.copy()
    changed[int(round(row[144] * sr)) :] = 0.9
    after = pocket.analyze(pocket.BINARY, changed, sr, policy="enrollment")[0]
    np.testing.assert_array_equal(after[4:141], row[4:141])


@pytest.mark.skipif(
    not pocket.BINARY.with_name("pocket_profile").exists(),
    reason="native core not built",
)
def test_native_v5_metric_negative_masks_and_legacy_payload(tmp_path):
    voices = [
        {"name": f"voice_{d}", "note": 36 + d, "rows": enrollment_rows(d)}
        for d in range(4)
    ]
    negatives = enrollment_rows(0, 8)
    negatives[:, 4:141] += 0.2
    doc = pocket.build_profile(voices, 48000, negatives)
    # Partial negative examples are legal on the wire; they cannot enter the
    # immediate rich vote. Both replicas must apply the same mask rule.
    at = 246 + 4 * pocket.VOICE_VALUES + 8 * 17
    for i in range(8):
        doc["values"][at + i * 31] = 3
    rows = np.concatenate([*(v["rows"][:3] for v in voices), negatives])
    rows[-1, 142:144] = 0
    path = tmp_path / "profile.txt"
    path.write_text(" ".join(map(str, doc["values"])))
    data = "\n".join(" ".join(map(str, row[4:144])) for row in rows)
    output = subprocess.run(
        [str(pocket.BINARY.with_name("pocket_profile")), str(path)],
        input=data,
        text=True,
        capture_output=True,
        check=True,
    )
    native = np.array(
        [list(map(int, line.split(","))) for line in output.stdout.splitlines()]
    )
    np.testing.assert_array_equal(native, pocket.predict_profile(doc, rows))
