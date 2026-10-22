"""Loader for the Amateur Vocal Percussion (AVP) dataset — cross-performer ground truth.

AVP (Delgado et al. 2019, CC BY 4.0, https://zenodo.org/records/3250230): 28
amateurs recorded on a MacBook Pro built-in mic — i.e. *this product's* input
conditions — with every onset hand-annotated as kick (``kd``), snare (``sd``),
closed hat (``hhc``) or open hat (``hho``). Per participant and modality there
are four **repetition** files (one class each, ~30 hits) and one
**improvisation** file (a real mixed groove). Three participants are shipped
in ``Discarded/`` and are skipped.

The archive is gitignored under ``datasets/avp/`` (230 MB). Fetch it with::

    curl -L -o datasets/avp/AVP_Dataset.zip \\
      "https://zenodo.org/records/3250230/files/AVP_Dataset.zip?download=1"
    unzip -q datasets/avp/AVP_Dataset.zip -d datasets/avp

Rows come back in the same shape ``eval.featurelab.labeled_onsets`` produces
(``audio``/``sr``/``t``/``label``/``take``), plus ``participant``, ``kind``
(``rep`` | ``improv``) and the raw 4-class ``raw_label``, so every featurizer
and classifier in the lab runs on it unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

import librosa
import numpy as np

from mouthflow import signal

REPO = Path(__file__).resolve().parent.parent
AVP_DIR = REPO / "datasets" / "avp" / "AVP_Dataset"
SR = signal._SR

# 4 raw classes -> the 3 classes mouthflow (and the MIT reference project) score.
LABEL3 = {"kd": "kick", "sd": "snare", "hhc": "hat", "hho": "hat"}
CLASSES = ("kick", "snare", "hat")

# The reference project's unseen-voice split (participants held out entirely).
MIT_HELDOUT = (5, 10, 16, 22, 28)

_NAME = re.compile(r"^P(\d+)_(Kick|Snare|HHclosed|HHopened|Improvisation)_(Personal|Fixed)\.csv$")


def available() -> bool:
    return AVP_DIR.is_dir()


def participants(modality: str = "Personal") -> list[int]:
    root = AVP_DIR / modality
    out = []
    for d in root.iterdir():
        m = re.match(r"^Participant_(\d+)$", d.name)
        if m and d.is_dir():
            out.append(int(m.group(1)))
    return sorted(out)


def _read_csv(path: Path) -> list[tuple[float, str]]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        t, lab = line.split(",")
        rows.append((float(t), lab.strip()))
    return rows


def load(
    modality: str = "Personal",
    only: list[int] | None = None,
    sr: int = SR,
) -> list[dict]:
    """Every annotated onset of ``modality`` as a labelled row (audio cached per file)."""
    if not available():
        raise FileNotFoundError(f"AVP dataset not found at {AVP_DIR} — see eval/avp.py docstring")
    rows: list[dict] = []
    for p in participants(modality):
        if only is not None and p not in only:
            continue
        pdir = AVP_DIR / modality / f"Participant_{p}"
        for csv in sorted(pdir.glob("*.csv")):
            m = _NAME.match(csv.name)
            if not m:
                continue
            kind = "improv" if m.group(2) == "Improvisation" else "rep"
            wav = csv.with_suffix(".wav")
            if not wav.exists():
                continue
            y, _ = librosa.load(str(wav), sr=sr, mono=True)
            take = f"P{p}_{m.group(2)}_{modality}"
            for t, raw in _read_csv(csv):
                if raw not in LABEL3:
                    continue
                rows.append(
                    {
                        "audio": y,
                        "sr": sr,
                        "t": float(t),
                        "label": LABEL3[raw],
                        "raw_label": raw,
                        "take": take,
                        "participant": p,
                        "kind": kind,
                        "file": str(wav),
                    }
                )
    return rows


def match_detected(rows: list[dict], tol: float = 0.050) -> list[dict]:
    """Replace each file's GT onset times with the pipeline's *detected* onsets.

    Greedy nearest match within ``tol`` seconds; unmatched GT onsets are dropped
    (reported by the caller as onset recall). This is the reference project's
    protocol — it scores the classifier on what the detector actually found.
    """
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    out: list[dict] = []
    for _, frows in by_file.items():
        y, sr = frows[0]["audio"], frows[0]["sr"]
        det = np.asarray(signal.detect_onsets(y, sr), dtype=float)
        used = np.zeros(len(det), dtype=bool)
        for r in sorted(frows, key=lambda r: r["t"]):
            if not len(det):
                continue
            d = np.abs(det - r["t"])
            d[used] = np.inf
            j = int(np.argmin(d))
            if d[j] <= tol:
                used[j] = True
                out.append({**r, "t": float(det[j]), "t_gt": r["t"]})
    return out
