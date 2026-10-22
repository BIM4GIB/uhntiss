"""Cross-performer drum-classifier eval on AVP — the first number not from one voice.

Reproduces the three protocols of the MIT-licensed reference project
(Frozen-In-Flames/beatboxing-to-midi, trained on the same dataset) so the
numbers are comparable, then runs every featurizer x classifier combination
the lab knows about:

  unseen   train on the repetition files of 20 participants, test on EVERY
           event of the 5 held out (5, 10, 16, 22, 28). Reference: 60.3 %.
  lopo     leave-one-participant-out over all 25 — the robust unseen-voice
           number (``unseen`` is one split; this is 25).
  improv   train on all participants' repetition files, test on their
           improvisations: voices seen, performances not. Reference: 71.9 %.
  calib    per participant: the product's enrollment story. ``user5`` = k-NN
           on 5 of the user's own hits per class only (reference: 74.5 %);
           ``user`` = all of the user's repetition hits (~30/class, the
           enrollment take); ``global+user`` = the global train set plus the
           user's repetition hits. Tested on the user's improvisation.

Onsets default to the hand-annotated times (classifier-only measurement);
``--onsets detected`` scores on what ``signal.detect_onsets`` finds, matched
to the annotation within 50 ms — the reference project's protocol — and
reports onset recall alongside.

Run: ``uv run python -m eval.avp_eval [--onsets gt|detected] [--feats ...] [--clf ...]``
"""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from pathlib import Path

# Cap BLAS threads BEFORE numpy loads: the models here are tiny matrices, and a
# default 16-thread pool spends its time spinning, not multiplying. Measured on
# this Mac: 4 uncapped runs in parallel produced no result in 71 min; capped
# at 3 threads a full MLP fit takes 0.8 s. Respect an explicit user setting.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np

from eval import avp
from eval.featurelab import baseline, bands_only, rich
from mouthflow import signal
from mouthflow.devices.drum.features import drum_features, drum_features_v2, logmel3, mfcc_stats


def drum_plus_logmel(audio, sr, t):
    return [*drum_features(audio, sr, t), *logmel3(audio, sr, t)]

CACHE = avp.REPO / "datasets" / "avp" / "cache"
CLASSES = list(avp.CLASSES)
K = 5


# --- featurizers: the production sets live in devices/drum/features ---------------

FEATURIZERS = {
    "baseline5": baseline,
    "bands9": bands_only,
    "rich12": rich,
    "drum10": drum_features,
    "mfcc39": mfcc_stats,
    "logmel74": logmel3,
    "drum10+logmel74": drum_plus_logmel,
    "all123": drum_features_v2,
}


# --- classifiers (all numpy; z-scoring is part of every model) --------------------

def _scaler(X):
    mean = X.mean(0)
    std = X.std(0)
    std[std == 0] = 1.0
    return mean, std


class KNN:
    def __init__(self, k=K):
        self.k = k

    def fit(self, X, y):
        self.mean, self.std = _scaler(X)
        self.Z = (X - self.mean) / self.std
        self.y = np.asarray(y)
        return self

    def predict(self, X):
        Z = (X - self.mean) / self.std
        out = []
        for z in Z:
            d = np.sum((self.Z - z) ** 2, axis=1)
            idx = np.argpartition(d, min(self.k, len(d) - 1))[: self.k]
            out.append(Counter(self.y[idx]).most_common(1)[0][0])
        return np.array(out)


def _onehot(y, classes):
    idx = {c: i for i, c in enumerate(classes)}
    Y = np.zeros((len(y), len(classes)))
    for i, lab in enumerate(y):
        Y[i, idx[lab]] = 1.0
    return Y


class LogReg:
    """Multinomial logistic regression, L2, full-batch Adam."""

    def __init__(self, l2=1e-3, epochs=300, lr=0.05, seed=0):
        self.l2, self.epochs, self.lr, self.seed = l2, epochs, lr, seed

    def fit(self, X, y):
        self.classes = CLASSES
        self.mean, self.std = _scaler(X)
        Z = (X - self.mean) / self.std
        Y = _onehot(y, self.classes)
        n, d = Z.shape
        rng = np.random.default_rng(self.seed)
        W = rng.normal(0, 0.01, (d, len(self.classes)))
        b = np.zeros(len(self.classes))
        mW, vW, mb, vb = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
        for it in range(1, self.epochs + 1):
            logits = Z @ W + b
            P = np.exp(logits - logits.max(1, keepdims=True))
            P /= P.sum(1, keepdims=True)
            G = (P - Y) / n
            gW = Z.T @ G + self.l2 * W
            gb = G.sum(0)
            for p, g, m, v in ((W, gW, mW, vW), (b, gb, mb, vb)):
                m[:] = 0.9 * m + 0.1 * g
                v[:] = 0.999 * v + 0.001 * g * g
                p -= self.lr * (m / (1 - 0.9**it)) / (np.sqrt(v / (1 - 0.999**it)) + 1e-8)
        self.W, self.b = W, b
        return self

    def predict(self, X):
        Z = (X - self.mean) / self.std
        return np.array(self.classes)[np.argmax(Z @ self.W + self.b, axis=1)]


class MLP:
    """One hidden layer (ReLU), L2, full-batch Adam — the reference project's shape."""

    def __init__(self, hidden=32, l2=1e-3, epochs=400, lr=0.01, seed=0):
        self.h, self.l2, self.epochs, self.lr, self.seed = hidden, l2, epochs, lr, seed

    def fit(self, X, y):
        self.classes = CLASSES
        self.mean, self.std = _scaler(X)
        Z = (X - self.mean) / self.std
        Y = _onehot(y, self.classes)
        n, d = Z.shape
        rng = np.random.default_rng(self.seed)
        params = [
            rng.normal(0, np.sqrt(2 / d), (d, self.h)),
            np.zeros(self.h),
            rng.normal(0, np.sqrt(2 / self.h), (self.h, len(self.classes))),
            np.zeros(len(self.classes)),
        ]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for it in range(1, self.epochs + 1):
            W1, b1, W2, b2 = params
            H = np.maximum(Z @ W1 + b1, 0)
            logits = H @ W2 + b2
            P = np.exp(logits - logits.max(1, keepdims=True))
            P /= P.sum(1, keepdims=True)
            G = (P - Y) / n
            gW2 = H.T @ G + self.l2 * W2
            gb2 = G.sum(0)
            GH = (G @ W2.T) * (H > 0)
            gW1 = Z.T @ GH + self.l2 * W1
            gb1 = GH.sum(0)
            for i, g in enumerate((gW1, gb1, gW2, gb2)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                params[i] -= self.lr * (m[i] / (1 - 0.9**it)) / (np.sqrt(v[i] / (1 - 0.999**it)) + 1e-8)
        self.params = params
        return self

    def embed(self, X):
        W1, b1, _, _ = self.params
        Z = (X - self.mean) / self.std
        return np.maximum(Z @ W1 + b1, 0)

    def proba(self, X):
        _, _, W2, b2 = self.params
        logits = self.embed(X) @ W2 + b2
        P = np.exp(logits - logits.max(1, keepdims=True))
        return P / P.sum(1, keepdims=True)

    def predict(self, X):
        return np.array(self.classes)[np.argmax(self.proba(X), axis=1)]


class MLPEns:
    """Average of 5 differently-seeded MLPs (variance reduction, no new capacity)."""

    def __init__(self, n=5, **kw):
        self.members = [MLP(seed=i, **kw) for i in range(n)]

    def fit(self, X, y):
        for m in self.members:
            m.fit(X, y)
        self.classes = self.members[0].classes
        return self

    def proba(self, X):
        return np.mean([m.proba(X) for m in self.members], axis=0)

    def predict(self, X):
        return np.array(self.classes)[np.argmax(self.proba(X), axis=1)]


CLASSIFIERS = {"knn": KNN, "logreg": LogReg, "mlp": MLP, "mlpens": MLPEns}


# --- feature cache ---------------------------------------------------------------

def featurize(rows, name, onsets: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.{onsets}.npz"
    key = np.array([f"{r['take']}:{r['t']:.6f}" for r in rows])
    if path.exists():
        z = np.load(path, allow_pickle=False)
        if z["key"].shape == key.shape and (z["key"] == key).all():
            return z["X"]
    fn = FEATURIZERS[name]
    t0 = time.time()
    X = np.array([fn(r["audio"], r["sr"], r["t"]) for r in rows], dtype=float)
    X[~np.isfinite(X)] = 0.0
    np.savez(path, X=X, key=key)
    print(f"  [{name}] extracted {X.shape} in {time.time() - t0:.1f}s")
    return X


# --- protocols -------------------------------------------------------------------

def _acc(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    overall = float((y_true == y_pred).mean()) if len(y_true) else float("nan")
    per = {}
    for c in CLASSES:
        m = y_true == c
        per[c] = float((y_pred[m] == c).mean()) if m.any() else float("nan")
    return overall, per


def _fit_predict(clf_name, Xtr, ytr, Xte):
    return CLASSIFIERS[clf_name]().fit(Xtr, ytr).predict(Xte)


def speaker_normalise(X, P):
    """Z-score each participant's features by their OWN (unlabelled) statistics.

    Legit at inference: a session's hits are available without labels, so a
    running per-user mean/std is free. Removes voice/mic offsets before the
    global model sees the vector."""
    Xn = X.copy()
    for p in set(P.tolist()):
        m = P == p
        mu, sd = _scaler(X[m])
        Xn[m] = (X[m] - mu) / sd
    return Xn


def context_column(rows):
    """Per-take relative loudness: log rms of the hit minus the take's median.

    The pipeline already normalises velocity per take, so this is available at
    inference. Hats are usually the quiet hits of a groove."""
    rms = np.array([signal.features_at(r["audio"], r["sr"], r["t"])["rms"] + 1e-6 for r in rows])
    files = np.array([r["file"] for r in rows])
    rel = np.zeros(len(rows))
    for f in set(files.tolist()):
        m = files == f
        rel[m] = np.log(rms[m]) - np.log(np.median(rms[m]))
    return rel[:, None]


def run_protocols(rows, X, clf_name, embed_knn: bool = False):
    y = np.array([r["label"] for r in rows])
    P = np.array([r["participant"] for r in rows])
    kind = np.array([r["kind"] for r in rows])
    parts = sorted(set(P.tolist()))
    res = {}

    # unseen (MIT split)
    tr = ~np.isin(P, avp.MIT_HELDOUT) & (kind == "rep")
    te = np.isin(P, avp.MIT_HELDOUT)
    res["unseen"] = _acc(y[te], _fit_predict(clf_name, X[tr], y[tr], X[te]))

    # lopo
    yt, yp = [], []
    for p in parts:
        tr = (P != p) & (kind == "rep")
        te = P == p
        yt.extend(y[te])
        yp.extend(_fit_predict(clf_name, X[tr], y[tr], X[te]))
    res["lopo"] = _acc(yt, yp)

    # improv
    tr = kind == "rep"
    te = kind == "improv"
    res["improv"] = _acc(y[te], _fit_predict(clf_name, X[tr], y[tr], X[te]))

    # calibration variants, tested on each participant's improvisation
    for variant in ("user5", "user", "global+user"):
        yt, yp = [], []
        for p in parts:
            te = (P == p) & (kind == "improv")
            if not te.any():
                continue
            own = (P == p) & (kind == "rep")
            if variant == "user5":
                idx = []
                for c in CLASSES:
                    idx.extend(np.where(own & (y == c))[0][:5].tolist())
                tr_idx = np.array(idx)
                pred = KNN().fit(X[tr_idx], y[tr_idx]).predict(X[te])
            elif variant == "user":
                pred = KNN().fit(X[own], y[own]).predict(X[te])
            else:
                tr = (kind == "rep")  # everyone's repetition hits, incl. this user's
                pred = _fit_predict(clf_name, X[tr], y[tr], X[te])
            yt.extend(y[te])
            yp.extend(pred)
        res[f"calib:{variant}"] = _acc(yt, yp)

    # the roadmap's enrollment recipe: a global MLP trained on OTHER performers
    # supplies the embedding; the user's own repetition hits are k-NN exemplars
    # in that space (the global head never sees this user).
    if embed_knn:
        for variant in ("embed-user5", "embed-user"):
            yt, yp = [], []
            for p in parts:
                te = (P == p) & (kind == "improv")
                own = (P == p) & (kind == "rep")
                if not te.any():
                    continue
                tr = (P != p) & (kind == "rep")
                g = MLP().fit(X[tr], y[tr])
                if variant == "embed-user5":
                    idx = []
                    for c in CLASSES:
                        idx.extend(np.where(own & (y == c))[0][:5].tolist())
                    own_idx = np.array(idx)
                else:
                    own_idx = np.where(own)[0]
                pred = KNN().fit(g.embed(X[own_idx]), y[own_idx]).predict(g.embed(X[te]))
                yt.extend(y[te])
                yp.extend(pred)
            res[f"calib:{variant}"] = _acc(yt, yp)
    return res


REFERENCE = {  # Frozen-In-Flames/beatboxing-to-midi, docs/training-avp.md
    "unseen": (0.603, {"kick": 0.760, "snare": 0.417, "hat": 0.612}),
    "improv": (0.719, {"kick": 0.808, "snare": 0.417, "hat": 0.796}),
    "calib:user5": (0.745, {"kick": 0.771, "snare": 0.537, "hat": 0.782}),
}


def _fmt(cell):
    overall, per = cell
    return f"{overall * 100:5.1f} (k{per['kick'] * 100:3.0f} s{per['snare'] * 100:3.0f} h{per['hat'] * 100:3.0f})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onsets", choices=["gt", "detected"], default="gt")
    ap.add_argument("--feats", nargs="*", default=list(FEATURIZERS))
    ap.add_argument("--clf", nargs="*", default=list(CLASSIFIERS))
    ap.add_argument("--modality", default="Personal")
    ap.add_argument("--speaker-norm", action="store_true", help="z-score features per participant (unlabelled stats)")
    ap.add_argument("--ctx", action="store_true", help="append per-take relative loudness column")
    ap.add_argument("--embed-knn", action="store_true", help="also run global-embedding + user k-NN calibration")
    args = ap.parse_args()

    if not avp.available():
        print(f"AVP dataset not found at {avp.AVP_DIR}; see eval/avp.py")
        return 2
    t0 = time.time()
    rows = avp.load(args.modality)
    n_gt = len(rows)
    if args.onsets == "detected":
        rows = avp.match_detected(rows)
        print(f"onset recall (detected within 50 ms of annotation): {len(rows)}/{n_gt} = {len(rows) / n_gt:.3f}")
    print(
        f"AVP {args.modality}: {len(rows)} events, {len(set(r['participant'] for r in rows))} participants, "
        f"{dict(Counter(r['label'] for r in rows))}  (loaded in {time.time() - t0:.1f}s)\n"
    )
    protos = ["unseen", "lopo", "improv", "calib:user5", "calib:user", "calib:global+user"]
    if args.embed_knn:
        protos += ["calib:embed-user5", "calib:embed-user"]
    ctx = context_column(rows) if args.ctx else None
    P = np.array([r["participant"] for r in rows])
    tag = ("+ctx" if args.ctx else "") + ("+spk" if args.speaker_norm else "")
    head = f"{'features':18s} {'clf':7s} " + " ".join(f"{p:>22s}" for p in protos)
    print(head)
    print("-" * len(head))
    print(f"{'REFERENCE (MIT)':18s} {'mlp':7s} " + " ".join(
        f"{_fmt(REFERENCE[p]):>22s}" if p in REFERENCE else f"{'-':>22s}" for p in protos))
    for fname in args.feats:
        X = featurize(rows, fname, args.onsets)
        if ctx is not None:
            X = np.hstack([X, ctx])
        if args.speaker_norm:
            X = speaker_normalise(X, P)
        for cname in args.clf:
            res = run_protocols(rows, X, cname, embed_knn=args.embed_knn)
            print(f"{(fname + tag)[:18]:18s} {cname:7s} " + " ".join(f"{_fmt(res[p]):>22s}" for p in protos), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
