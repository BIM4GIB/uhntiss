"""Train a per-user drum classifier (k-NN) from calibration + in-context data.

Two data sources, same 5 timbre features (loudness excluded):
- calibration/<label>.wav : isolated one-shots (clean class anchors)
- mimic/<take>.labeled.json : in-context hits auto-labeled by the mimic-a-beat
  harness (realistic timbre across tempos)

k-NN (not nearest-centroid) because a drum class is multi-modal: a hat sounds
bright when played slowly and darker when played fast, so a single centroid
blurs it. k-NN keeps the exemplars and votes by nearest neighbours.

Reports held-out-take accuracy (does in-context from other takes generalise?)
and combined leave-one-out, then writes mouthflow/drum_model.json.

Run: ``uv run python -m eval.train_classifier``
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import librosa

from mouthflow import transcribe as T

REPO = Path(__file__).resolve().parent.parent
CAL_DIR = REPO / "calibration"
MIMIC_DIR = REPO / "mimic"
MODEL_OUT = REPO / "mouthflow" / "drum_model.json"

FEATURES = ["centroid", "sub100_ratio", "decay_s", "zcr", "flatness"]
RMS_FLOOR = 0.005
K = 5
LABEL_TO_PITCH = {"kick": 36, "snare": 38, "hat": 42, "clap": 39}


def _norm(stem: str) -> str:
    return "hat" if stem.startswith("hat") else stem


def load_isolated():
    rows = []
    for wav in sorted(CAL_DIR.glob("*.wav")):
        lab = _norm(wav.stem)
        yf, _ = librosa.load(str(wav), sr=T._SR, mono=True)
        for t in T._detect_onsets(yf, T._SR):
            f = T._features_at(yf, T._SR, t)
            if f["rms"] >= RMS_FLOOR:
                rows.append(([f[k] for k in FEATURES], lab, "isolated"))
    return rows


def load_incontext():
    rows = []
    for j in sorted(MIMIC_DIR.glob("*.labeled.json")):
        take = j.stem.replace(".labeled", "")
        for s in json.loads(j.read_text()):
            rows.append((s["x"], _norm(s["y"]), take))
    return rows


def _scaler(X):
    n, d = len(X), len(FEATURES)
    mean = [sum(r[j] for r in X) / n for j in range(d)]
    std = [(sum((r[j] - mean[j]) ** 2 for r in X) / n) ** 0.5 or 1.0 for j in range(d)]
    return mean, std


def _z(x, mean, std):
    return [(x[j] - mean[j]) / std[j] for j in range(len(FEATURES))]


def _knn(Ztr, ytr, z, k=K):
    d = sorted(range(len(Ztr)), key=lambda i: sum((Ztr[i][j] - z[j]) ** 2 for j in range(len(z))))
    return Counter(ytr[i] for i in d[:k]).most_common(1)[0][0]


def _fit_eval(train, test, k=K):
    X = [r[0] for r in train]
    mean, std = _scaler(X)
    Ztr = [_z(r[0], mean, std) for r in train]
    ytr = [r[1] for r in train]
    ok = sum(_knn(Ztr, ytr, _z(x, mean, std), k) == y for x, y, _ in test)
    return ok, len(test)


def main() -> int:
    iso = load_isolated()
    ctx = load_incontext()
    allr = iso + ctx
    print(f"isolated:  {dict(Counter(r[1] for r in iso))}  ({len(iso)})")
    print(f"in-context:{dict(Counter(r[1] for r in ctx))}  ({len(ctx)})\n")

    print("held-out take (k-NN accuracy on that take):")
    for t in sorted({r[2] for r in ctx}):
        test = [r for r in ctx if r[2] == t]
        a = _fit_eval(iso + [r for r in ctx if r[2] != t], test)
        print(f"  {t:8} iso+other-takes {a[0]}/{a[1]} = {a[0]/a[1]:.2f}")

    # combined leave-one-out
    mean, std = _scaler([r[0] for r in allr])
    Z = [_z(r[0], mean, std) for r in allr]
    y = [r[1] for r in allr]
    correct = sum(
        _knn([Z[k] for k in range(len(Z)) if k != i], [y[k] for k in range(len(y)) if k != i], Z[i]) == y[i]
        for i in range(len(Z))
    )
    print(f"\ncombined model leave-one-out (k-NN, k={K}): {correct}/{len(Z)} = {correct/len(Z):.2f}")

    MODEL_OUT.write_text(json.dumps({
        "type": "knn", "k": K,
        "features": FEATURES, "mean": mean, "std": std,
        "classes": {lab: LABEL_TO_PITCH.get(lab, 39) for lab in sorted(set(y))},
        "exemplars": Z, "labels": y, "rms_floor": RMS_FLOOR,
    }))
    print(f"wrote model -> {MODEL_OUT.relative_to(REPO)} ({len(Z)} exemplars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
