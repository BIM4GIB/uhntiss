"""AVP loader + cross-performer harness: offline self-tests (no dataset needed)."""

from __future__ import annotations

import numpy as np
import pytest

from eval import avp, avp_eval


def test_label_map_merges_hats():
    assert avp.LABEL3["hhc"] == avp.LABEL3["hho"] == "hat"
    assert avp.LABEL3["kd"] == "kick" and avp.LABEL3["sd"] == "snare"


def test_filename_pattern_and_csv_parse(tmp_path):
    csv = tmp_path / "P7_Improvisation_Personal.csv"
    csv.write_text("0.10,kd\n0.55,sd\n\n1.00,hhc\n1.40,hho\n")
    m = avp._NAME.match(csv.name)
    assert m and m.group(1) == "7" and m.group(2) == "Improvisation"
    rows = avp._read_csv(csv)
    assert rows == [(0.10, "kd"), (0.55, "sd"), (1.00, "hhc"), (1.40, "hho")]


def test_match_detected_uses_pipeline_onsets_and_drops_unmatched():
    sr = 44100
    y = np.zeros(sr * 2, dtype=np.float32)
    rng = np.random.default_rng(0)
    for t in (0.30, 0.90, 1.50):  # three clicks the detector will find
        s = int(t * sr)
        y[s : s + 800] = rng.normal(0, 0.5, 800).astype(np.float32) * np.exp(-np.arange(800) / 150)
    rows = [
        {"audio": y, "sr": sr, "t": t, "label": "kick", "take": "x", "file": "f"}
        for t in (0.30, 0.90, 1.50, 1.90)  # 1.90 has no attack -> dropped
    ]
    out = avp.match_detected(rows, tol=0.05)
    assert len(out) == 3
    assert all(abs(r["t"] - r["t_gt"]) <= 0.05 for r in out)


@pytest.mark.parametrize("clf", ["knn", "logreg", "mlp"])
def test_classifiers_separate_three_blobs(clf):
    rng = np.random.default_rng(1)
    centres = {"kick": (0, 0), "snare": (6, 0), "hat": (0, 6)}
    X, y = [], []
    for lab, c in centres.items():
        X.append(rng.normal(c, 0.7, (60, 2)))
        y += [lab] * 60
    X = np.vstack(X)
    y = np.array(y)
    idx = rng.permutation(len(y))
    tr, te = idx[:120], idx[120:]
    pred = avp_eval._fit_predict(clf, X[tr], y[tr], X[te])
    assert (pred == y[te]).mean() >= 0.95


def test_acc_reports_overall_and_per_class():
    overall, per = avp_eval._acc(["kick", "kick", "hat", "snare"], ["kick", "hat", "hat", "snare"])
    assert overall == 0.75
    assert per == {"kick": 0.5, "snare": 1.0, "hat": 1.0}


@pytest.mark.skipif(not avp.available(), reason="AVP dataset not downloaded (datasets/avp)")
def test_avp_loads_25_participants_three_classes():
    rows = avp.load("Personal", only=[5])
    assert rows and {r["label"] for r in rows} == set(avp.CLASSES)
    assert {r["kind"] for r in rows} == {"rep", "improv"}
