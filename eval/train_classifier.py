"""Train a per-user drum classifier from calibration clips.

Each ``calibration/<label>.wav`` is an isolated take of one drum sound, so
every onset in it is a labelled example. We extract the same timbre features
``transcribe`` uses, standardise them, and fit a nearest-centroid model.
Loudness (rms) is deliberately excluded — that's velocity, not class.

Reports leave-one-out accuracy + a confusion matrix so we can see which
classes actually separate, then writes the model to mouthflow/drum_model.json.

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
MODEL_OUT = REPO / "mouthflow" / "drum_model.json"

FEATURES = ["centroid", "sub100_ratio", "decay_s", "zcr", "flatness"]
RMS_FLOOR = 0.005  # drop near-silent onsets

LABEL_TO_PITCH = {"kick": 36, "snare": 38, "hat": 42, "clap": 39}

# Closed vs open hat aren't separable with the current 120ms timbre features
# (no sustain cue) and open-hat is under-sampled, so collapse both to "hat"
# (GM closed hat, 42). Distinguishing them is a follow-up: add a sustain
# feature + more open-hat data.
def _norm_label(stem: str) -> str:
    return "hat" if stem.startswith("hat") else stem


def _samples() -> tuple[list[list[float]], list[str]]:
    X: list[list[float]] = []
    y: list[str] = []
    for wav in sorted(CAL_DIR.glob("*.wav")):
        label = _norm_label(wav.stem)
        yf, _ = librosa.load(str(wav), sr=T._SR, mono=True)
        for t in T._detect_onsets(yf, T._SR):
            f = T._features_at(yf, T._SR, t)
            if f["rms"] < RMS_FLOOR:
                continue
            X.append([f[k] for k in FEATURES])
            y.append(label)
    return X, y


def _standardise(X: list[list[float]]):
    n, d = len(X), len(FEATURES)
    mean = [sum(row[j] for row in X) / n for j in range(d)]
    std = [
        (sum((row[j] - mean[j]) ** 2 for row in X) / n) ** 0.5 or 1.0 for j in range(d)
    ]
    Z = [[(row[j] - mean[j]) / std[j] for j in range(d)] for row in X]
    return Z, mean, std


def _centroids(Z, y, labels):
    cen = {}
    for lab in labels:
        rows = [Z[i] for i in range(len(Z)) if y[i] == lab]
        cen[lab] = [sum(r[j] for r in rows) / len(rows) for j in range(len(FEATURES))]
    return cen


def _nearest(z, cen):
    best, bestd = None, float("inf")
    for lab, c in cen.items():
        d = sum((z[j] - c[j]) ** 2 for j in range(len(z)))
        if d < bestd:
            best, bestd = lab, d
    return best


def main() -> int:
    X, y = _samples()
    labels = sorted(set(y))
    print(f"calibration: {dict(Counter(y))}  ({len(X)} labelled onsets)\n")

    Z, mean, std = _standardise(X)

    # Leave-one-out: recompute centroids without sample i each time.
    conf = Counter()
    correct = 0
    for i in range(len(Z)):
        sub_Z = [Z[k] for k in range(len(Z)) if k != i]
        sub_y = [y[k] for k in range(len(y)) if k != i]
        pred = _nearest(Z[i], _centroids(sub_Z, sub_y, sorted(set(sub_y))))
        conf[(y[i], pred)] += 1
        correct += pred == y[i]
    print(f"leave-one-out accuracy: {correct}/{len(Z)} = {correct/len(Z):.2f}\n")

    print("confusion (true -> pred):")
    print("            " + "  ".join(f"{l[:9]:>9}" for l in labels))
    for t in labels:
        row = "  ".join(f"{conf[(t, p)]:>9}" for p in labels)
        print(f"  {t:9} {row}")

    # Fit final model on all data and persist.
    cen = _centroids(Z, y, labels)
    model = {
        "features": FEATURES,
        "mean": mean,
        "std": std,
        "classes": {lab: LABEL_TO_PITCH.get(lab, 39) for lab in labels},
        "centroids": cen,
        "rms_floor": RMS_FLOOR,
    }
    MODEL_OUT.write_text(json.dumps(model, indent=2))
    print(f"\nwrote model -> {MODEL_OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
