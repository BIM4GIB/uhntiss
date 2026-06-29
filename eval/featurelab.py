"""Feature-experiment harness for the drum classifier.

Re-extracts labelled onsets straight from the source audio (calibration
one-shots + mimic takes via their ground-truth grid), so candidate feature
sets can be cross-validated against the honest held-out-take metric WITHOUT
re-scoring takes, touching ``signal.features_at``, or retraining.

A featurizer is just ``f(audio, sr, t) -> list[float]``. Run ``main`` to bake
several off against the current 5-feature baseline.

Run: ``uv run python -m eval.featurelab``
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import librosa
import numpy as np

from mouthflow import signal
from mouthflow.devices.drum.classify import _classify_heuristic
from mimic.take import _match

# heuristic GM pitch -> class label, used to break grid phase ambiguity
_H2LAB = {36: "kick", 38: "snare", 42: "hat", 46: "hat"}

REPO = Path(__file__).resolve().parent.parent
CAL_DIR = REPO / "calibration"
MIMIC_DIR = REPO / "mimic"
SR = signal._SR
RMS_FLOOR = 0.005
K = 5


# --- labelled-onset extraction (audio + onset time + label + take) ----------

def _norm(stem: str) -> str:
    return "hat" if stem.startswith("hat") else stem


def labeled_onsets() -> list[dict]:
    rows: list[dict] = []
    # calibration one-shots — clean class anchors (take = "iso")
    for wav in sorted(CAL_DIR.glob("*.wav")):
        y, _ = librosa.load(str(wav), sr=SR, mono=True)
        for t in signal.detect_onsets(y, SR):
            if signal.features_at(y, SR, t)["rms"] >= RMS_FLOOR:
                rows.append({"audio": y, "sr": SR, "t": float(t), "label": _norm(wav.stem), "take": "iso"})
    # mimic takes — in-context hits, labelled via the reaction-aligned grid
    import json

    for grid_path in sorted(MIMIC_DIR.glob("*.grid.json")):
        take = grid_path.stem.replace(".grid", "")
        wav_path = MIMIC_DIR / f"{take}.mimic.wav"
        if not wav_path.exists():
            continue
        grid = json.loads(grid_path.read_text())["grid"]
        gt = np.array([t for t, _ in grid])
        gl = [lab for _, lab in grid]
        y, _ = librosa.load(str(wav_path), sr=SR, mono=True)
        feats = {t: signal.features_at(y, SR, t) for t in signal.detect_onsets(y, SR)}
        onsets = [t for t, f in feats.items() if f["rms"] >= RMS_FLOOR]
        # Align by the offset that maximizes *timbre agreement* (each matched
        # onset's heuristic class == its grid label). Raw match-count is
        # ambiguous to a half-beat on the dense hat grid (it slides kicks onto
        # hat slots); requiring timbre agreement breaks that tie. Grid tempo
        # must match the take's record tempo for this to lock.
        hlab = {t: _H2LAB.get(_classify_heuristic(feats[t])) for t in onsets}

        def _score(d):
            return sum(1 for o, j in _match(onsets, gt, d, tol=0.10) if hlab[o] == _norm(gl[j]))

        offset = max(np.arange(-0.6, 0.6, 0.005), key=_score)
        for o, j in _match(onsets, gt, offset, tol=0.10):
            rows.append({"audio": y, "sr": SR, "t": float(o), "label": _norm(gl[j]), "take": take})
    return rows


# --- cross-validation -------------------------------------------------------

def _scaler(X):
    A = np.asarray(X)
    mean = A.mean(0)
    std = A.std(0)
    std[std == 0] = 1.0
    return mean, std


def _knn(Ztr, ytr, z, k=K):
    d = np.sum((Ztr - z) ** 2, axis=1)
    order = np.argsort(d)[:k]
    return Counter(ytr[i] for i in order).most_common(1)[0][0]


def cross_val(featurize, name: str, rows: list[dict] | None = None) -> dict:
    rows = rows or labeled_onsets()
    X = np.array([featurize(r["audio"], r["sr"], r["t"]) for r in rows], dtype=float)
    y = [r["label"] for r in rows]
    takes = [r["take"] for r in rows]
    labels = sorted(set(y))

    # held-out-take: iso always in train, test on each mimic take
    heldout = {}
    for take in sorted(t for t in set(takes) if t != "iso"):
        tr = [i for i in range(len(rows)) if takes[i] != take]
        te = [i for i in range(len(rows)) if takes[i] == take]
        mean, std = _scaler(X[tr])
        Ztr = (X[tr] - mean) / std
        ytr = [y[i] for i in tr]
        ok = sum(_knn(Ztr, ytr, (X[i] - mean) / std) == y[i] for i in te)
        heldout[take] = ok / len(te)

    # leave-one-out + confusion
    mean, std = _scaler(X)
    Z = (X - mean) / std
    conf = {t: Counter() for t in labels}
    correct = 0
    for i in range(len(Z)):
        mask = np.arange(len(Z)) != i
        pred = _knn(Z[mask], [y[k] for k in range(len(y)) if k != i], Z[i])
        conf[y[i]][pred] += 1
        correct += pred == y[i]
    loo = correct / len(Z)

    ho_str = "  ".join(f"{t}={v:.2f}" for t, v in heldout.items())
    ho_mean = float(np.mean(list(heldout.values()))) if heldout else 0.0
    print(f"[{name}]  feats={X.shape[1]}  LOO={loo:.3f}  held-out: {ho_str}  (mean {ho_mean:.3f})")
    for t in labels:
        tot = sum(conf[t].values())
        cells = " ".join(f"{p}:{conf[t][p]}" for p in labels)
        print(f"     {t:5} acc={conf[t][t] / tot:.2f}  | {cells}")
    return {"loo": loo, "heldout_mean": ho_mean, "heldout": heldout}


# --- candidate featurizers --------------------------------------------------

_BASELINE = ["centroid", "sub100_ratio", "decay_s", "zcr", "flatness"]


def baseline(audio, sr, t):
    """The current production 5 features (correctness check)."""
    f = signal.features_at(audio, sr, t)
    return [f[k] for k in _BASELINE]


def _frame(audio, sr, t, dur):
    s = int(t * sr)
    return audio[s : min(s + int(dur * sr), len(audio))]


def _bands(frame, sr, n_fft):
    spec = np.abs(np.fft.rfft(frame, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, d=1 / sr)
    total = spec.sum() + 1e-9
    edges = [0, 100, 250, 800, 2500, 8000, sr / 2]
    return [float(spec[(freqs >= lo) & (freqs < hi)].sum() / total) for lo, hi in zip(edges, edges[1:])]


def rich(audio, sr, t):
    """Baseline + multi-band energy ratios + a longer-window sustain ratio."""
    f = signal.features_at(audio, sr, t)
    frame = _frame(audio, sr, t, 0.120)
    n_fft = min(1024, 1 << max(1, (len(frame) - 1)).bit_length()) if len(frame) >= 64 else 256
    bands = _bands(frame, sr, n_fft) if len(frame) >= 64 else [0.0] * 6
    # sustain: energy in 120-300ms relative to 0-120ms (open hat / snare tail)
    early = _frame(audio, sr, t, 0.120)
    late = audio[int((t + 0.120) * sr) : int((t + 0.300) * sr)]
    e_early = float(np.sqrt(np.mean(early**2))) if early.size else 0.0
    e_late = float(np.sqrt(np.mean(late**2))) if late.size else 0.0
    sustain = e_late / (e_early + 1e-9)
    return [f["centroid"], f["sub100_ratio"], f["decay_s"], f["zcr"], f["flatness"], *bands, sustain]


def bands_only(audio, sr, t):
    """Multi-band ratios + zcr/flatness — no hand-picked sub100/centroid."""
    f = signal.features_at(audio, sr, t)
    frame = _frame(audio, sr, t, 0.120)
    n_fft = min(1024, 1 << max(1, (len(frame) - 1)).bit_length()) if len(frame) >= 64 else 256
    bands = _bands(frame, sr, n_fft) if len(frame) >= 64 else [0.0] * 6
    return [*bands, f["zcr"], f["flatness"], f["decay_s"]]


def main() -> int:
    rows = labeled_onsets()
    print(f"labelled onsets: {len(rows)}  {dict(Counter(r['label'] for r in rows))}\n")
    for fn, nm in [(baseline, "baseline-5"), (bands_only, "bands+zcr+flat+decay"), (rich, "rich-12")]:
        cross_val(fn, nm, rows)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
