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
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from mouthflow import signal
from mouthflow.devices.drum import classify as drum_classify

# Built-in sounds -> GM pitch. Any OTHER name is a user sound: a performer's
# vocabulary is not three drums (the bar is a virtuoso with twenty), so
# `add_clip("rimshot", …, note=37)` registers a new class in the manifest and
# the k-NN emits that note. `classes()` is the live table.
BUILTIN_CLASSES: dict[str, int] = {
    "kick": drum_classify.GM_KICK,
    "snare": drum_classify.GM_SNARE,
    "hat": drum_classify.GM_HAT_CLOSED,
    "hat_open": drum_classify.GM_HAT_OPEN,
    # "noise" = everything the mic hears that must NOT become a drum: the
    # performer singing, talking, breathing. Its verdict is DROP. Measured
    # 2026-10-25: a global noise class dropped half of this performer's real
    # hits; a per-user one from a single hum take cut false triggers on the
    # other hum take 76 -> 12/min with zero real hits lost.
    "noise": drum_classify.DROP,
}
NOISE = "noise"
NOISE_MIN_VOTES = 4  # of K: silencing a real hit costs more than one sung note getting through
CORE = ("kick", "snare", "hat")  # what a stock kit + the planner expect; advisory, not required
MIN_CLASSES = 2  # a model needs something to tell apart
# Free GM percussion notes handed out when a new sound is added without --note
# (a Drum Rack pad exists for each; the performer can remap with `enroll map`).
AUTO_NOTES = (37, 39, 41, 43, 45, 47, 48, 49, 50, 51, 53, 54, 56, 57, 59, 60, 61, 62, 63, 64)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_ALIASES = {
    "k": "kick", "kd": "kick", "bd": "kick", "bass": "kick",
    "s": "snare", "sd": "snare", "sn": "snare",
    "h": "hat", "hh": "hat", "hhc": "hat", "hat_closed": "hat", "hat-closed": "hat",
    "closed": "hat", "closedhat": "hat", "hihat": "hat", "hi-hat": "hat", "hats": "hat",
    "o": "hat_open", "open": "hat_open", "hho": "hat_open", "hat-open": "hat_open",
    "openhat": "hat_open", "open_hat": "hat_open", "oh": "hat_open",
    "voice": "noise", "sing": "noise", "singing": "noise", "talk": "noise", "speech": "noise",
    "breath": "noise", "breathing": "noise", "hum": "noise", "ignore": "noise", "not_a_drum": "noise",
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


def _canonical(name: str) -> str:
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(key, key)


def classes() -> dict[str, int]:
    """Live class table: built-ins (possibly remapped) + the manifest's user sounds."""
    table = dict(BUILTIN_CLASSES)
    for name, spec in _load_manifest().get("classes", {}).items():
        try:
            table[name] = int(spec["note"] if isinstance(spec, dict) else spec)
        except (KeyError, TypeError, ValueError):
            continue
    return table


def normalise_class(name: str, note: int | str | None = None) -> str:
    """Resolve a class name; register a NEW user sound when ``note`` is given.

    ``note`` may be a MIDI number or ``"auto"`` (next free GM percussion note).
    Unknown names without a note raise, so a typo can't silently create a class.
    """
    key = _canonical(name)
    table = classes()
    if key in table:
        return key
    if note is None:
        known = ", ".join(sorted(table))
        raise ValueError(
            f"unknown sound {name!r}; known: {known}. To add it as a new sound pass a MIDI note "
            f"(e.g. --note 37) or --note auto"
        )
    if not _NAME_RE.match(key):
        raise ValueError(f"sound name {name!r} must be letters/digits/underscore, start with a letter, max 24 chars")
    set_class_note(key, note)
    return key


def set_class_note(name: str, note: int | str) -> int:
    """Map a sound to a MIDI note (new user sound, or remap any class). Returns the note."""
    key = _canonical(name)
    table = classes()
    if isinstance(note, str) and note.strip().lower() == "auto":
        used = set(table.values())
        free = [n for n in AUTO_NOTES if n not in used]
        if not free:
            raise ValueError("no free auto notes left; pass an explicit --note")
        midi = free[0]
    else:
        try:
            midi = int(note)
        except (TypeError, ValueError):
            raise ValueError(f"note must be a MIDI number 0-127 or 'auto', got {note!r}")
        if not 0 <= midi <= 127:
            raise ValueError(f"note must be 0-127, got {midi}")
    if key not in table and not _NAME_RE.match(key):
        raise ValueError(f"sound name {name!r} must be letters/digits/underscore, start with a letter, max 24 chars")
    m = _load_manifest()
    m.setdefault("classes", {})[key] = {"note": midi}
    _save_manifest(m)
    return midi


# --- manifest ---------------------------------------------------------------------

def _manifest_path() -> Path:
    return ENROLL_DIR / _MANIFEST


def _load_manifest() -> dict:
    try:
        m = json.loads(_manifest_path().read_text())
        if isinstance(m, dict) and isinstance(m.get("clips"), list):
            m.setdefault("classes", {})
            return m
    except (OSError, ValueError):
        pass
    return {"clips": [], "classes": {}}


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

def add_clip(cls: str, src: Path, source: str = "file", note: int | str | None = None) -> dict:
    """Ingest one one-class clip: detect hits, copy into the enroll dir, record it.

    ``note`` registers ``cls`` as a new user sound (MIDI number or "auto") when
    it isn't known yet; known names ignore it (use ``set_class_note`` to remap).
    """
    import soundfile as sf

    cls = normalise_class(cls, note)
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
        "note": classes()[cls],
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
    """Hits per class (with notes), readiness, and the installed user model (if any)."""
    m = _load_manifest()
    table = classes()
    per: dict[str, dict] = {c: {"clips": 0, "hits": 0, "note": n, "builtin": c in BUILTIN_CLASSES} for c, n in table.items()}
    for e in m["clips"]:
        c = e.get("class")
        if c in per and Path(e.get("path", "")).exists():
            per[c]["clips"] += 1
            per[c]["hits"] += int(e.get("hits", 0))
    recorded = [c for c, v in per.items() if v["hits"] > 0 and c != NOISE]
    core_missing = [c for c in CORE if per[c]["hits"] == 0]
    thin = [c for c in per if 0 < per[c]["hits"] < MIN_HITS]
    model = drum_classify._load_json(drum_classify._USER_MODEL_PATH)
    return {
        "classes": per,
        "recorded": recorded,
        "missing": core_missing,  # advisory: what a stock kit / the planner expect
        "thin": thin,
        "ready": len(recorded) >= MIN_CLASSES,
        "model": (model or {}).get("source") if model else None,
        "model_path": str(drum_classify._USER_MODEL_PATH),
    }


def format_status(st: dict) -> list[str]:
    lines = []
    for c, v in st["classes"].items():
        if v["hits"] == 0 and not v["builtin"]:
            continue  # a mapped-but-unrecorded user sound
        tag = "" if v["hits"] >= TARGET_HITS else (f" (aim for {TARGET_HITS})" if v["hits"] else "")
        kind = "" if v["builtin"] else " [your sound]"
        if c == NOISE:
            note_s, kind = "DROP   ", " [ignore: sing/talk/breathe]"
            tag = "" if v["hits"] else " — record 20 s of your voice so singing never triggers drums"
        else:
            note_s = f"note {v['note']:3d}"
        lines.append(f"{c:12s} {note_s}  {v['hits']:3d} hits in {v['clips']} clip(s){tag}{kind}")
    if not st["ready"]:
        lines.append(f"record at least {MIN_CLASSES} sounds (one clip each), then `enroll train`")
    elif st["missing"]:
        lines.append(f"note: no {', '.join(st['missing'])} yet — a stock kit expects them; your own sounds still train")
    if st["ready"] and st["model"]:
        acc = st["model"].get("cv_accuracy")
        lines.append(
            f"user model installed ({st['model'].get('enrolled_at', '?')}"
            + (f", cv {acc * 100:.0f} %" if acc is not None else "")
            + ")"
        )
    elif st["ready"]:
        lines.append("ready — run `enroll train`")
    return lines


def _rows() -> list[tuple[np.ndarray, float, str]]:
    m = _load_manifest()
    rows: list[tuple[np.ndarray, float, str]] = []
    table = classes()
    for e in m["clips"]:
        p = Path(e.get("path", ""))
        if not p.exists() or e.get("class") not in table:
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
    if len([c for c in have if c != NOISE]) < MIN_CLASSES:
        got = ", ".join(sorted(have)) or "nothing"
        raise EnrollError(
            f"need at least {MIN_CLASSES} sounds with hits to train (have: {got}) — "
            f"record one clip per sound first (`enroll status`)"
        )
    # A thin open-hat class hurts more than it helps: 6 open-hat exemplars
    # against 19 closed ones turned a real groove's hats mostly "open". Fold
    # it into closed hat (AVP merges them anyway) until there are enough.
    # User sounds have nowhere to fold; they stay and are reported as thin.
    folded: list[str] = []
    if 0 < have.get("hat_open", 0) < MIN_HITS and have.get("hat", 0):
        rows = [(y, t, "hat" if lab == "hat_open" else lab) for y, t, lab in rows]
        folded.append("hat_open")
    have = Counter(lab for _, _, lab in rows)
    table = classes()
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
        "classes": {c: table[c] for c in sorted(have)},
        "exemplars": np.round(Z, 5).tolist(),
        "labels": labels,
        "rms_floor": RMS_FLOOR,
        "noise_min_votes": NOISE_MIN_VOTES,
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
        "thin": [c for c in have if have[c] < MIN_HITS and c != NOISE],
        "noise_hits": int(have.get(NOISE, 0)),
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


CLASSES_BY_DRUM = {0: "kick", 1: "snare", 2: "hat"}


def export_pocket(binary: Path | str | None = None, sr: int = 48000, gate: float = 0.010,
                  reject_clips: list[Path] | None = None) -> dict:
    """Export every enrolled voice + MIDI mapping; keep up to 30 representatives per voice.

    Clips are one class each. Breath/speech controls can be supplied explicitly;
    neither a mixed calibration take nor a held-out evaluation take is enrollment.
    """
    import hashlib
    import librosa
    from mouthflow import pocket

    bin_path = pocket.find_binary(binary)
    table = classes()
    by_class: dict[str, list[np.ndarray]] = {}
    sources = []
    negatives = []
    def read_rows(path):
        y = _load_audio(path)
        if sr != SR:
            y = librosa.resample(y, orig_sr=SR, target_sr=sr).astype(np.float32)
        return pocket.analyze(bin_path, y, sr, gate, policy="enrollment")
    for e in _load_manifest()["clips"]:
        path, cls = Path(e.get("path", "")), e.get("class")
        if not path.is_file() or cls not in table:
            continue
        rows = read_rows(path)
        if cls == NOISE:
            negatives.extend(rows)
        else:
            by_class.setdefault(cls, []).append(rows)
        sources.append({"class": cls, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "pocket_hits": len(rows)})
    # Stable K/S/H-first order preserves the built-in pads; every other name
    # remains a distinct voice, including open hats and user-defined sounds.
    names = sorted(by_class, key=lambda c: (c not in ("kick", "snare", "hat"),
                                           ("kick", "snare", "hat").index(c) if c in ("kick", "snare", "hat") else c))
    voices = [{"name": name, "note": table[name], "rows": np.concatenate(by_class[name])} for name in names]
    for source in reject_clips or []:
        path = Path(source)
        if not path.is_file():
            raise pocket.PocketError(f"rejection clip not found: {path}")
        rows = read_rows(path)
        negatives.extend(rows)
        sources.append({"class": "__reject__", "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "pocket_hits": len(rows)})
    doc = pocket.build_profile(voices, sr, np.asarray(negatives).reshape(-1, 145))
    doc["source"] = {"kind": "mouthflow-enroll", "clips": sources,
                     "exported_at": datetime.now().isoformat(timespec="seconds")}
    doc["gate"] = gate
    written = pocket.write_profile_document(doc)
    counts = {v["name"]: len(v["rows"]) for v in voices}
    return {"written": written["written"], "detected": counts, "skipped": {}, "hits_per_drum": counts,
            "reject_examples": doc["reject_examples"]}
