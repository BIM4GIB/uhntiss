"""Is Pocket's ceiling the feature vector or the 10 ms causal window?

``eval/avp_pocket.py`` showed Pocket's Teach metric saturating at ~72 % on
25 performers while the Python ``all123`` features reach ~81 % on the very
same detected onsets — but ``all123`` looks at 200 ms of audio after the
onset and Pocket decides after 10 ms. This script separates the two:

For every matched Pocket detection on AVP it computes ONE feature family
(level-normalised log-mel + MFCC + zcr + centroid + pre-context energy
ratio) over causal windows of 10 / 20 / 40 / 80 / 200 ms starting at the
detection, and runs the enrollment protocol (that performer's repetition
hits -> k-NN -> their improvisation). Two reference rows use the same k-NN:
Pocket's own 17 features (from the detector), and ``all123``.

If 10 ms already lands near the 200 ms number, the feature vector is the
lever and Pocket can keep its latency contract. If accuracy climbs steeply
with the window, the window is the limit and the loop-recording path wants
a delayed decision (sound the provisional voice at once, re-label at commit).

Run: ``uv run python -m eval.avp_window [--sr 44100] [--windows 10 20 40 80 200]``
"""

from __future__ import annotations

import argparse
import os
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np

from eval import avp
from eval.avp_eval import KNN
from eval.avp_pocket import LABEL_IDX, _acc, _fmt, _match
from mouthflow import pocket

N_MELS = 24
N_MFCC = 13
PRE_MS = 30.0


def _mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float = 60.0, fmax: float | None = None) -> np.ndarray:
    import librosa

    return librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=n_mels, fmin=fmin, fmax=fmax or sr / 2)


_FB_CACHE: dict[tuple[int, int], np.ndarray] = {}


def window_features(y: np.ndarray, sr: int, t: float, win_ms: float, pre_ms: float = PRE_MS) -> np.ndarray:
    """Causal features over [t, t + win_ms): log-mel shape (24) + MFCC (13) + zcr + centroid + pre-context ratio."""
    from scipy.fftpack import dct

    s = int(round(t * sr))
    n = max(32, int(round(win_ms / 1000.0 * sr)))
    fr = y[s : s + n]
    if len(fr) < n:
        fr = np.pad(fr, (0, n - len(fr)))
    fr = fr.astype(np.float64) * np.hanning(n)
    n_fft = max(256, 1 << (n - 1).bit_length())
    spec = np.abs(np.fft.rfft(fr, n=n_fft)) ** 2 + 1e-12
    key = (sr, n_fft)
    if key not in _FB_CACHE:
        _FB_CACHE[key] = _mel_filterbank(sr, n_fft, N_MELS)
    mel = _FB_CACHE[key] @ spec + 1e-12
    logmel = np.log(mel / mel.sum())
    mfcc = dct(np.log(mel), type=2, norm="ortho")[:N_MFCC]
    zcr = float(np.mean(np.abs(np.diff(np.sign(fr))) > 0))
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    centroid = float((freqs * spec).sum() / spec.sum()) / (sr / 2)
    pre_n = int(pre_ms / 1000.0 * sr)
    pre = y[max(0, s - pre_n) : s].astype(np.float64)
    e_pre = float(np.mean(pre**2)) if len(pre) else 0.0
    e_win = float(np.mean((fr / np.hanning(n).mean()) ** 2))
    ratio = np.log((e_win + 1e-9) / (e_pre + 1e-9))
    return np.concatenate([logmel, mfcc, [zcr, centroid, ratio]])


def sliced_features(y: np.ndarray, sr: int, t: float, edges_ms=(0.0, 10.0, 40.0, 80.0)) -> np.ndarray:
    """Causal attack/body/tail slices (all123's structure) inside one longer window."""
    parts = []
    for a, b in zip(edges_ms, edges_ms[1:]):
        parts.append(window_features(y, sr, t + a / 1000.0, b - a, pre_ms=PRE_MS if a == 0 else 0.0))
    return np.concatenate(parts)


def detect_and_match(binary, sr: int, gate: float, modality: str):
    """Pocket detections per AVP file, matched to the annotations (same as avp_pocket)."""
    import librosa

    rows = avp.load(modality)
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    files = {}
    for f, frows in by_file.items():
        y, y_sr = frows[0]["audio"], frows[0]["sr"]
        y_in = librosa.resample(y, orig_sr=y_sr, target_sr=sr) if sr != y_sr else y
        det = pocket.accepted(pocket.analyze(binary, y_in, sr, gate))
        gt_t = np.array([r["t"] for r in frows])
        pairs = _match(det[:, 0], gt_t) if len(det) else []
        files[f] = {
            "participant": frows[0]["participant"],
            "kind": frows[0]["kind"],
            "det": det,
            "pairs": pairs,
            "gt_label": np.array([LABEL_IDX[r["label"]] for r in frows]),
            "audio": y_in,
            "sr": sr,
        }
    return files


def enrollment_accuracy(files: dict, featurize, per_class: int | None = None, k: int = 5) -> tuple[float, dict]:
    """Per performer: k-NN on their matched repetition hits (optionally only the first
    ``per_class`` per voice — Pocket's Teach keeps 3), scored on their matched improvisation hits."""
    yt, yp = [], []
    parts = sorted({fi["participant"] for fi in files.values()})
    for p in parts:
        Xtr, ytr, Xte, yte = [], [], [], []
        for fi in sorted((fi for fi in files.values() if fi["participant"] == p), key=lambda fi: fi["kind"]):
            for i, j in sorted(fi["pairs"]):
                vec = featurize(fi, i)
                lab = int(fi["gt_label"][j])
                if fi["kind"] == "rep":
                    if per_class is not None and ytr.count(lab) >= per_class:
                        continue
                    Xtr.append(vec)
                    ytr.append(lab)
                else:
                    Xte.append(vec)
                    yte.append(lab)
        if not Xte or len(set(ytr)) < 3:
            continue
        pred = KNN(k=k).fit(np.array(Xtr), np.array([avp.CLASSES[i] for i in ytr])).predict(np.array(Xte))
        yt.extend(yte)
        yp.extend(LABEL_IDX[c] for c in pred)
    return _acc(yt, yp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--gate", type=float, default=0.010)
    ap.add_argument("--windows", type=float, nargs="*", default=[10, 20, 40, 80, 200])
    ap.add_argument("--modality", default="Personal")
    ap.add_argument("--binary", default=None)
    args = ap.parse_args()
    if not avp.available():
        print(f"AVP dataset not found at {avp.AVP_DIR}; see eval/avp.py")
        return 2
    binary = pocket.find_binary(args.binary)
    t0 = time.time()
    files = detect_and_match(binary, args.sr, args.gate, args.modality)
    n_pairs = sum(len(fi["pairs"]) for fi in files.values())
    print(f"{len(files)} files, {n_pairs} matched detections at {args.sr} Hz ({time.time() - t0:.1f}s)\n")
    print(f"{'features (k-NN enrollment, same onsets)':44s} {'acc (per class)':>24s}")
    # references
    res = enrollment_accuracy(files, lambda fi, i: fi["det"][i, 4:])
    print(f"{'Pocket 17 (10 ms causal + 30 ms context)':44s} {_fmt(res):>24s}")
    from mouthflow.devices.drum.features import extract

    res = enrollment_accuracy(files, lambda fi, i: np.array(extract("all123", fi["audio"], fi["sr"], float(fi["det"][i, 0]))))
    print(f"{'all123 (up to 200 ms, non-causal design)':44s} {_fmt(res):>24s}")
    for w in args.windows:
        t1 = time.time()
        res = enrollment_accuracy(files, lambda fi, i, w=w: window_features(fi["audio"], fi["sr"], float(fi["det"][i, 0]), w))
        print(f"{f'window {w:g} ms causal (logmel+mfcc+zcr+cent+pre)':44s} {_fmt(res):>24s}   {time.time() - t1:.0f}s", flush=True)
    for edges in ((0, 10, 40), (0, 10, 40, 80), (0, 10, 40, 120), (0, 30, 90, 200)):
        res = enrollment_accuracy(files, lambda fi, i, e=edges: sliced_features(fi["audio"], fi["sr"], float(fi["det"][i, 0]), e))
        label = "/".join(str(x) for x in edges[1:])
        print(f"{f'slices 0-{label} ms causal (same family x{len(edges) - 1})':44s} {_fmt(res):>24s}", flush=True)
    # Teach-sized enrollment (3 per voice, 3 nearest) for the two candidates vs Pocket's own features
    print()
    print(f"{'3 examples per voice, 3-NN (Teach-sized)':44s} {'acc (per class)':>24s}")
    for name, fn in (
        ("Pocket 17", lambda fi, i: fi["det"][i, 4:]),
        ("window 10 ms causal", lambda fi, i: window_features(fi["audio"], fi["sr"], float(fi["det"][i, 0]), 10)),
        ("slices 0-10/40/80 ms causal", lambda fi, i: sliced_features(fi["audio"], fi["sr"], float(fi["det"][i, 0]), (0, 10, 40, 80))),
    ):
        res = enrollment_accuracy(files, fn, per_class=3, k=3)
        print(f"{name:44s} {_fmt(res):>24s}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
