"""The negative set: audio that must NEVER become a drum hit, and the gate it trains.

The open-mic problem in numbers (2026-10-25): the drums device fires 72 false
hits/min on hummed bass takes (Pocket: 37/min) because neither classifier can
say "not a drum". This module collects negatives from what exists without
anyone recording, and measures a *noise gate*: a 4-class global MLP
(kick / snare / hat / noise) whose noise verdict turns an onset into DROP.

Negative sources (``collect``):
  avp-unmatched   onsets mouthflow detects in AVP files with no annotated hit
                  within 75 ms — breaths, lip noise, room, from 25 performers
  hum             onsets in hummed bass takes (datasets/bass/*.hum.wav)
  reference       onsets in the synthesized bass references (sustained tones)
  breath-synth    band-limited noise swells (Pocket's breath-control recipe)
  speech          LibriSpeech dev-clean under datasets/librispeech/ (CC BY 4.0;
                  curl -L -o datasets/librispeech/dev-clean.tar.gz
                  https://www.openslr.org/resources/12/dev-clean.tar.gz && tar -xzf …)

Protocol (``evaluate``): leave-one-performer-out on AVP for the drum classes
PLUS leave-one-SOURCE-out on the negatives — the gate is scored on a negative
source it never trained on, so "it learned René's hum" can't pass as
"it rejects singing". Reported per threshold: AVP drum accuracy (3-class, on
hits the gate let through), AVP hits lost to the gate, and false triggers per
minute on the held-out negative source.

Findings (2026-10-25): talking gives 212 false hits/min (Pocket 170/min).
A global gate rejects speech 230 -> 40/min and hums -> 2.5/min held out,
but silences 60-70 % of the performer's real fixture hits (``--fixtures``),
so the product path is the per-user ``noise`` enrollment sound, whose clip
must include talking as well as singing and breathing.

Run: ``uv run python -m eval.negatives [--thresholds 0.5 0.7 0.9] [--fixtures]``
"""

from __future__ import annotations

import argparse
import glob
import os
import time
from collections import defaultdict
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np

from eval import avp
from eval.avp_eval import MLP, featurize
from mouthflow import signal
from mouthflow.devices.drum.features import extract

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "datasets" / "avp" / "cache" / "negatives.all123.npz"
SPEECH_DIR = REPO / "datasets" / "librispeech"
FEATURE_SET = "all123"
RMS_FLOOR = 0.005
MATCH_TOL = 0.075
CLASSES4 = ["kick", "snare", "hat", "noise"]


def _onsets(y: np.ndarray, sr: int) -> list[float]:
    return [float(t) for t in signal.detect_onsets(y, sr) if signal.features_at(y, sr, float(t))["rms"] >= RMS_FLOOR]


def _breath_synth(n_clips: int = 40, sr: int = signal._SR, seed: int = 0) -> list[np.ndarray]:
    """Breath-shaped controls: band-limited noise with slow swells (Pocket's recipe: band, ramp, gain)."""
    from scipy.signal import butter, sosfilt

    rng = np.random.default_rng(seed)
    clips = []
    for _ in range(n_clips):
        dur = rng.uniform(0.6, 2.0)
        n = int(dur * sr)
        lo = rng.uniform(80, 400)
        hi = rng.uniform(max(lo * 2, 600), 6000)
        sos = butter(4, [lo, hi], btype="band", fs=sr, output="sos")
        x = sosfilt(sos, rng.normal(0, 1, n))
        ramp = int(rng.uniform(0.02, 0.3) * sr)
        env = np.ones(n)
        env[:ramp] = np.linspace(0, 1, ramp)
        env[-ramp:] *= np.linspace(1, 0, ramp)
        x = x / (np.abs(x).max() + 1e-9) * rng.uniform(0.05, 0.5)
        clips.append((x * env).astype(np.float32))
    return clips


def collect(force: bool = False) -> dict:
    """Feature bank of negatives: X (n x 123), source labels, seconds of audio per source."""
    if CACHE.exists() and not force:
        z = np.load(CACHE, allow_pickle=True)
        return {"X": z["X"], "source": z["source"], "seconds": z["seconds"].item()}
    X: list[list[float]] = []
    src: list[str] = []
    seconds: dict[str, float] = defaultdict(float)
    t0 = time.time()

    # avp-unmatched
    if avp.available():
        by_file: dict[str, list[dict]] = defaultdict(list)
        for r in avp.load("Personal"):
            by_file[r["file"]].append(r)
        for f, frows in by_file.items():
            y, sr = frows[0]["audio"], frows[0]["sr"]
            gt = np.array([r["t"] for r in frows])
            seconds["avp-unmatched"] += 0.0  # not a rate source: it is interleaved with real hits
            for t in _onsets(y, sr):
                if np.min(np.abs(gt - t)) > MATCH_TOL:
                    X.append(extract(FEATURE_SET, y, sr, t))
                    src.append("avp-unmatched")

    # hum + reference
    import librosa

    for pattern, name in (("datasets/bass/*.hum.wav", "hum"), ("datasets/bass/*.reference.wav", "reference")):
        for f in sorted(glob.glob(str(REPO / pattern))):
            y, sr = librosa.load(f, sr=signal._SR, mono=True)
            seconds[name] += len(y) / sr
            for t in _onsets(y, sr):
                X.append(extract(FEATURE_SET, y, sr, t))
                src.append(name)

    # breath-synth
    for clip in _breath_synth():
        seconds["breath-synth"] += len(clip) / signal._SR
        for t in _onsets(clip, signal._SR):
            X.append(extract(FEATURE_SET, clip, signal._SR, t))
            src.append("breath-synth")

    # speech (optional)
    if SPEECH_DIR.is_dir():
        files = sorted(SPEECH_DIR.rglob("*.flac"))[:120]  # ~10 min is plenty for a rate
        for f in files:
            y, sr = librosa.load(str(f), sr=signal._SR, mono=True)
            seconds["speech"] += len(y) / sr
            for t in _onsets(y, sr):
                X.append(extract(FEATURE_SET, y, sr, t))
                src.append("speech")

    Xa = np.array(X, dtype=float)
    Xa[~np.isfinite(Xa)] = 0.0
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, X=Xa, source=np.array(src), seconds=np.array(dict(seconds), dtype=object))
    print(f"collected {len(src)} negative onsets in {time.time() - t0:.1f}s: "
          + ", ".join(f"{s} {list(src).count(s)}" for s in sorted(set(src))))
    return {"X": Xa, "source": np.array(src), "seconds": dict(seconds)}


class MLP4(MLP):
    """Same MLP, four outputs."""

    def fit(self, X, y):
        import eval.avp_eval as ae

        saved = ae.CLASSES
        ae.CLASSES = CLASSES4
        try:
            return super().fit(X, y)
        finally:
            ae.CLASSES = saved


def _gate(proba: np.ndarray, thr: float, margin: bool) -> np.ndarray:
    """Noise verdict. Plain: P(noise) >= thr. Margin: additionally no drum class is confident
    (max drum prob < 0.5) — a hit the drum side is sure about is never dropped."""
    noise = proba[:, CLASSES4.index("noise")] >= thr
    if not margin:
        return noise
    return noise & (proba[:, :3].max(axis=1) < 0.5)


def evaluate(thresholds: list[float], margin: bool = False) -> None:
    neg = collect()
    rows = avp.load("Personal")
    Xp = featurize(rows, FEATURE_SET, "gt")
    yp = np.array([r["label"] for r in rows])
    P = np.array([r["participant"] for r in rows])
    kind = np.array([r["kind"] for r in rows])
    Xn, srcn, secs = neg["X"], neg["source"], neg["seconds"]
    rate_sources = [s for s in sorted(set(srcn)) if secs.get(s, 0) > 0]
    parts = sorted(set(P.tolist()))

    print(f"\nAVP hits {len(yp)}; negatives {len(srcn)} ({', '.join(f'{s} {int((srcn == s).sum())}' for s in sorted(set(srcn)))})")
    print(f"{'gate':>6s}  {'AVP acc (gated hits)':>22s}  {'AVP hits lost':>14s}  " + "  ".join(f"{s + ' FT/min':>18s}" for s in rate_sources))

    # Reference row: no gate (3-class lopo as before, nothing rejected)
    for thr in [None] + thresholds:
        acc_num = acc_den = lost = 0
        lost_by = {"kick": 0, "snare": 0, "hat": 0}
        for p in parts:
            tr = (P != p) & (kind == "rep")
            te = P == p
            if thr is None:
                m = MLP().fit(Xp[tr], yp[tr])
                pred = m.predict(Xp[te])
            else:
                Xtr = np.vstack([Xp[tr], Xn])  # negatives from ALL sources for the drum-side check
                ytr = np.concatenate([yp[tr], np.full(len(Xn), "noise")])
                m = MLP4().fit(Xtr, ytr)
                proba = m.proba(Xp[te])
                noise_i = CLASSES4.index("noise")
                gated = _gate(proba, thr, margin)
                lost += int(gated.sum())
                for c in ("kick", "snare", "hat"):
                    lost_by[c] += int((gated & (yp[te] == c)).sum())
                drum = np.array(CLASSES4)[np.argmax(proba[:, :3], axis=1)]
                pred = drum[~gated]
                yte = yp[te][~gated]
                acc_num += int((pred == yte).sum()); acc_den += len(yte)
                continue
            acc_num += int((pred == yp[te]).sum()); acc_den += len(yp[te])
        tag = "none" if thr is None else (f"{thr}{'m' if margin else ''}")
        line = f"{tag:>6}  {acc_num / max(1, acc_den) * 100:21.1f}%  {lost:5d} ({lost / len(yp) * 100:4.1f}% k{lost_by['kick']} s{lost_by['snare']} h{lost_by['hat']})"
        # leave-one-SOURCE-out false-trigger rate per negative source
        for s in rate_sources:
            if thr is None:
                ft = int((srcn == s).sum())  # every onset becomes a hit without a gate
            else:
                tr_n = srcn != s
                Xtr = np.vstack([Xp[kind == "rep"], Xn[tr_n]])
                ytr = np.concatenate([yp[kind == "rep"], np.full(int(tr_n.sum()), "noise")])
                m = MLP4().fit(Xtr, ytr)
                proba = m.proba(Xn[srcn == s])
                ft = int((~_gate(proba, thr, margin)).sum())
            line += f"  {ft / secs[s] * 60:10.1f} ({ft:3d}/{int((srcn == s).sum()):3d})"
        print(line, flush=True)


def fixture_retention(thresholds: list[float]) -> None:
    """The global gate's cost on THIS performer: real onsets on the drum oracle's fixture
    takes that a gate trained on AVP + every negative source would silence.
    (2026-10-25: ~50 % at any threshold — why the shipped noise class is per-user.)"""
    import librosa

    neg = collect()
    rows = avp.load("Personal")
    Xp = featurize(rows, FEATURE_SET, "gt")
    yp = np.array([r["label"] for r in rows])
    m = MLP4().fit(np.vstack([Xp, neg["X"]]), np.concatenate([yp, np.full(len(neg["X"]), "noise")]))
    print(f"\nglobal gate trained on AVP + all negatives ({', '.join(sorted(set(neg['source'])))}):")
    for f in sorted(glob.glob(str(REPO / "tests" / "fixtures" / "clips" / "*.wav"))):
        y, sr = librosa.load(f, sr=signal._SR, mono=True)
        ons = _onsets(y, sr)
        X = np.array([extract(FEATURE_SET, y, sr, t) for t in ons])
        X[~np.isfinite(X)] = 0.0
        pr = m.proba(X)
        cells = "  ".join(f"@{thr}: {int(_gate(pr, thr, False).sum()):2d}/{len(ons)} real onsets silenced" for thr in thresholds)
        print(f"  {Path(f).name:24s} {cells}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", type=float, nargs="*", default=[0.5, 0.7, 0.9])
    ap.add_argument("--recollect", action="store_true")
    ap.add_argument("--margin", action="store_true", help="also require no confident drum class")
    ap.add_argument("--fixtures", action="store_true", help="also report real onsets silenced on this performer's fixture takes")
    args = ap.parse_args()
    if not avp.available():
        print("AVP dataset needed (eval/avp.py)")
        return 2
    if args.recollect:
        collect(force=True)
    evaluate(args.thresholds, margin=args.margin)
    if args.fixtures:
        fixture_retention(args.thresholds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
