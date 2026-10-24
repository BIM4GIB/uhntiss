"""Per-user drum enrollment: teach mouthflow YOUR kick, snare and hats.

The classifier's best tier is a k-NN over the performer's own hits
(``devices/drum/classify.py``). On the AVP cross-performer dataset that is
worth +13 points over the best global model (83 % vs 68–70 % on real grooves),
and ~30 hits per class beats 5 per class by 3 points. This module is the
flow that produces those hits:

1. One class per clip. You record (in Live, or with the mic) a short clip of
   ONLY kicks, then only snares, then only hats — ~12 s, ~30 hits each. The
   clip IS the label: nothing is annotated, and nothing can be swapped (a
   scripted multi-section take had its snare and hat sections swapped on
   the first try). Open hat is an optional fourth clip — on AVP a separate
   open-hat class costs nothing on kick/snare/hat and separates from closed
   hats at 72–87 %.
2. ``add_clip`` detects the hits in each clip (same onset detector as the
   pipeline), copies the audio into ``~/.mouthflow/enroll/`` and records it
   in a manifest.
3. ``train`` extracts the production feature set, cross-validates (stratified
   folds over hits — an optimistic number, hits come from one clip per
   class; expect a real groove a few points lower) and writes the per-user
   model to ``~/.mouthflow/drum_model.json``, which the classifier prefers
   over the packaged one.

CLI: ``mouthflow enroll clip|file|record <class>``, ``enroll train``,
``enroll status``, ``enroll reset --yes``. The drums panel has the same
buttons (selected-clip ingest).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from mouthflow import signal
from mouthflow.devices.drum import classify as drum_classify

# class -> GM pitch. kick/snare/hat are required; hat_open is optional.
CLASSES: dict[str, int] = {
    "kick": drum_classify.GM_KICK,
    "snare": drum_classify.GM_SNARE,
    "hat": drum_classify.GM_HAT_CLOSED,
    "hat_open": drum_classify.GM_HAT_OPEN,
}
REQUIRED = ("kick", "snare", "hat")
_ALIASES = {
    "k": "kick", "kd": "kick", "bd": "kick", "bass": "kick",
    "s": "snare", "sd": "snare", "sn": "snare",
    "h": "hat", "hh": "hat", "hhc": "hat", "hat_closed": "hat", "hat-closed": "hat",
    "closed": "hat", "closedhat": "hat", "hihat": "hat", "hi-hat": "hat", "hats": "hat",
    "o": "hat_open", "open": "hat_open", "hho": "hat_open", "hat-open": "hat_open",
    "openhat": "hat_open", "open_hat": "hat_open", "oh": "hat_open",
}

ENROLL_DIR = Path.home() / ".mouthflow" / "enroll"
_MANIFEST = "manifest.json"
SR = signal._SR
RMS_FLOOR = 0.005
K = 5
FEATURE_SET = "all123"
TARGET_HITS = 30  # what the AVP study says is enough
MIN_HITS = 12  # below this, warn — the model will be thin for that class


class EnrollError(RuntimeError):
    """A user-facing enrollment problem (missing class, no hits, …)."""


def normalise_class(name: str) -> str:
    key = name.strip().lower().replace(" ", "_")
    key = _ALIASES.get(key, key)
    if key not in CLASSES:
        raise ValueError(f"unknown drum class {name!r}; use one of {', '.join(CLASSES)}")
    return key


# --- manifest ---------------------------------------------------------------------

def _manifest_path() -> Path:
    return ENROLL_DIR / _MANIFEST


def _load_manifest() -> dict:
    try:
        m = json.loads(_manifest_path().read_text())
        if isinstance(m, dict) and isinstance(m.get("clips"), list):
            return m
    except (OSError, ValueError):
        pass
    return {"clips": []}


def _save_manifest(m: dict) -> None:
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(_manifest_path(), json.dumps(m, indent=2))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- hits -------------------------------------------------------------------------

def _hits(y: np.ndarray, sr: int) -> list[float]:
    """Onsets loud enough to be hits (same detector + floor as the pipeline)."""
    return [
        float(t)
        for t in signal.detect_onsets(y, sr)
        if signal.features_at(y, sr, float(t))["rms"] >= RMS_FLOOR
    ]


def _load_audio(path: Path) -> np.ndarray:
    import librosa

    y, _ = librosa.load(str(path), sr=SR, mono=True)  # any rate/channels -> 44.1 k mono
    return np.asarray(y, dtype=np.float32)


# --- public API -------------------------------------------------------------------

def add_clip(cls: str, src: Path, source: str = "file") -> dict:
    """Ingest one one-class clip: detect hits, copy into the enroll dir, record it."""
    import soundfile as sf

    cls = normalise_class(cls)
    src = Path(src)
    if not src.exists():
        raise EnrollError(f"audio file not found: {src}")
    y = _load_audio(src)
    hits = _hits(y, SR)
    if not hits:
        raise EnrollError(f"no hits detected in {src.name} — is the clip silent, or the input muted?")
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = ENROLL_DIR / f"{cls}-{stamp}.wav"
    i = 1
    while dest.exists():
        dest = ENROLL_DIR / f"{cls}-{stamp}-{i}.wav"
        i += 1
    sf.write(dest, y, SR, subtype="PCM_16")
    entry = {
        "class": cls,
        "path": str(dest),
        "source": str(src),
        "kind": source,
        "hits": len(hits),
        "duration_s": round(len(y) / SR, 2),
        "added": datetime.now().isoformat(timespec="seconds"),
    }
    m = _load_manifest()
    m["clips"].append(entry)
    _save_manifest(m)
    return entry


def status() -> dict:
    """Hits per class, clip counts, readiness, and the installed user model (if any)."""
    m = _load_manifest()
    per: dict[str, dict] = {c: {"clips": 0, "hits": 0} for c in CLASSES}
    for e in m["clips"]:
        c = e.get("class")
        if c in per and Path(e.get("path", "")).exists():
            per[c]["clips"] += 1
            per[c]["hits"] += int(e.get("hits", 0))
    missing = [c for c in REQUIRED if per[c]["hits"] == 0]
    thin = [c for c in CLASSES if 0 < per[c]["hits"] < MIN_HITS]
    model = drum_classify._load_json(drum_classify._USER_MODEL_PATH)
    return {
        "classes": per,
        "missing": missing,
        "thin": thin,
        "ready": not missing,
        "model": (model or {}).get("source") if model else None,
        "model_path": str(drum_classify._USER_MODEL_PATH),
    }


def format_status(st: dict) -> list[str]:
    lines = []
    for c, v in st["classes"].items():
        tag = "" if v["hits"] >= TARGET_HITS else (f" (aim for {TARGET_HITS})" if v["hits"] else "")
        opt = " [optional]" if c not in REQUIRED else ""
        lines.append(f"{c:9s} {v['hits']:3d} hits in {v['clips']} clip(s){tag}{opt}")
    if st["missing"]:
        lines.append(f"missing: {', '.join(st['missing'])} — record them, then `enroll train`")
    elif st["model"]:
        acc = st["model"].get("cv_accuracy")
        lines.append(
            f"user model installed ({st['model'].get('enrolled_at', '?')}"
            + (f", cv {acc * 100:.0f} %" if acc is not None else "")
            + ")"
        )
    else:
        lines.append("all required classes recorded — run `enroll train`")
    return lines


def _rows() -> list[tuple[np.ndarray, float, str]]:
    m = _load_manifest()
    rows: list[tuple[np.ndarray, float, str]] = []
    for e in m["clips"]:
        p = Path(e.get("path", ""))
        if not p.exists() or e.get("class") not in CLASSES:
            continue
        y = _load_audio(p)
        rows.extend((y, t, e["class"]) for t in _hits(y, SR))
    return rows


def _knn_vote(Ztr: np.ndarray, ytr: list[str], z: np.ndarray, k: int) -> str:
    d = np.sum((Ztr - z) ** 2, axis=1)
    idx = np.argsort(d)[:k]
    return Counter(ytr[i] for i in idx).most_common(1)[0][0]


def _cross_val(Z: np.ndarray, y: list[str], k: int, folds: int, seed: int) -> dict | None:
    """Stratified k-fold over hits. Optimistic (one clip per class), but catches a broken enrollment."""
    y_arr = np.asarray(y)
    counts = Counter(y)
    folds = min(folds, min(counts.values()))
    if folds < 2:
        return None
    rng = np.random.default_rng(seed)
    fold_of = np.zeros(len(y), dtype=int)
    for c in counts:
        idx = np.where(y_arr == c)[0]
        rng.shuffle(idx)
        for f, chunk in enumerate(np.array_split(idx, folds)):
            fold_of[chunk] = f
    conf: dict[str, Counter] = {c: Counter() for c in counts}
    for f in range(folds):
        tr, te = fold_of != f, fold_of == f
        ytr = [y[i] for i in np.where(tr)[0]]
        for i in np.where(te)[0]:
            conf[y[i]][_knn_vote(Z[tr], ytr, Z[i], k)] += 1
    correct = sum(conf[c][c] for c in conf)
    per = {c: conf[c][c] / max(1, sum(conf[c].values())) for c in conf}
    return {
        "accuracy": correct / len(y),
        "per_class": per,
        "confusion": {c: dict(conf[c]) for c in conf},
        "folds": folds,
    }


def train(k: int = K, folds: int = 5, seed: int = 0) -> dict:
    """Extract features from every enrolled hit, cross-validate, write the user model."""
    from mouthflow.devices.drum.features import FEATURE_SETS

    rows = _rows()
    have = Counter(lab for _, _, lab in rows)
    missing = [c for c in REQUIRED if not have.get(c)]
    if missing:
        raise EnrollError(
            f"missing {', '.join(missing)} — record one clip per class first (`enroll status`)"
        )
    # A thin OPTIONAL class hurts more than it helps: 6 open-hat exemplars
    # against 19 closed ones turned a real groove's hats mostly "open". Fold
    # it into closed hat (AVP merges them anyway) until there are enough.
    folded: list[str] = []
    for opt in [c for c in CLASSES if c not in REQUIRED]:
        if 0 < have.get(opt, 0) < MIN_HITS:
            rows = [(y, t, "hat" if lab == opt else lab) for y, t, lab in rows]
            folded.append(opt)
    have = Counter(lab for _, _, lab in rows)
    names, fn = FEATURE_SETS[FEATURE_SET]
    X = np.array([fn(y, SR, t) for y, t, _ in rows], dtype=float)
    X[~np.isfinite(X)] = 0.0
    labels = [lab for _, _, lab in rows]
    mean = X.mean(0)
    std = X.std(0)
    std[std == 0] = 1.0
    Z = (X - mean) / std
    cv = _cross_val(Z, labels, k, folds, seed)
    m = _load_manifest()
    model = {
        "type": "knn",
        "k": k,
        "feature_set": FEATURE_SET,
        "features": names,
        "mean": np.round(mean, 6).tolist(),
        "std": np.round(std, 6).tolist(),
        "classes": {c: CLASSES[c] for c in sorted(have)},
        "exemplars": np.round(Z, 5).tolist(),
        "labels": labels,
        "rms_floor": RMS_FLOOR,
        "source": {
            "kind": "enrollment",
            "enrolled_at": datetime.now().isoformat(timespec="seconds"),
            "hits_per_class": {c: int(have[c]) for c in sorted(have)},
            "clips": [{"class": e["class"], "path": e["path"], "hits": e["hits"]} for e in m["clips"]],
            "cv_accuracy": None if cv is None else round(cv["accuracy"], 4),
            "cv_per_class": None if cv is None else {c: round(v, 4) for c, v in cv["per_class"].items()},
            "folded": folded,
        },
    }
    _atomic_write(drum_classify._USER_MODEL_PATH, json.dumps(model))
    drum_classify._MODEL = drum_classify._UNSET  # next classify() in this process reloads
    return {
        "path": str(drum_classify._USER_MODEL_PATH),
        "n": len(rows),
        "hits_per_class": {c: int(have[c]) for c in sorted(have)},
        "thin": [c for c in have if have[c] < MIN_HITS],
        "folded": folded,
        "cv": cv,
    }


def reset() -> None:
    """Delete the enrollment clips and the user model (the packaged model takes over again)."""
    shutil.rmtree(ENROLL_DIR, ignore_errors=True)
    try:
        drum_classify._USER_MODEL_PATH.unlink()
    except FileNotFoundError:
        pass
    drum_classify._MODEL = drum_classify._UNSET
