"""Offline bridge to Pocket's native detection, enrollment and state contracts.

V4 keeps its exact 156-double, three-example interchange. V5 carries 2–16
named MIDI voices with up to 30 examples, causal 10/40/80 ms slices and a
personal reject bank. Python fits profiles from enrollment only; the C++
engine owns live inference. ``predict_profile`` is the parity reference.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
HEADER = REPO / "native" / "pocket" / "include" / "pocket" / "attack_model.hpp"
BINARY = REPO / "native" / "pocket" / "build" / "pocket_analyze"
BUILD_HINT = (
    "cmake -S native/pocket -B native/pocket/build -DCMAKE_BUILD_TYPE=Release "
    "&& cmake --build native/pocket/build -j"
)

N_FEATURES = 17
DRUMS = ("kick", "snare", "hat")  # Pocket's Drum enum order; 3 = noise/rejected
NOISE = 3
PROFILE_EXAMPLES = 3  # Legacy v4 interchange and quick Teach.
PROFILE_VALUES = 3 * (1 + PROFILE_EXAMPLES * N_FEATURES)  # == pocket::profileValues
STATE_VERSION = 5


def profile_path() -> Path:
    """Canonical profile location (resolved at call time so tests can isolate HOME)."""
    return Path.home() / ".mouthflow" / "pocket_profile.json"


# Max resolves a bare filename through its search path; the user Library is
# on it by default, so the device's LOAD PROFILE button finds the file there.
MAX_LIBRARY_GLOB = "Documents/Max */Library"
MAX_PROFILE_NAME = "uhntiss_pocket_profile.json"


class PocketError(RuntimeError):
    """A user-facing Pocket bridge problem (binary missing, bad header, …)."""


def find_binary(explicit: Path | str | None = None) -> Path:
    p = Path(explicit) if explicit else BINARY
    if not p.exists():
        raise PocketError(
            f"pocket_analyze not found at {p}; build it with: {BUILD_HINT}"
        )
    return p


def analyze(
    binary: Path,
    samples: np.ndarray,
    sr: int,
    gate: float = 0.010,
    policy: str | None = None,
) -> np.ndarray:
    """Run Pocket's detector over ``samples`` (mono float). Rows: onset seconds, factory drum, velocity, decision seconds, f1..f17.

    slices/enrollment append 120 commit features, 3 observed lengths, observed window end (not dispatch time)."""
    args = [str(binary), str(sr), str(gate)] + ([policy] if policy else [])
    p = subprocess.run(
        args,
        input=np.asarray(samples, dtype="<f4").tobytes(),
        capture_output=True,
        check=True,
    )
    rows = [
        list(map(float, line.split(",")))
        for line in p.stdout.decode().splitlines()
        if line.strip()
    ]
    width = (
        145
        if policy in ("slices", "enrollment")
        else 4 + N_FEATURES + (6 if policy == "transient" else 0)
    )
    if any(len(row) != width for row in rows):
        raise PocketError(
            f"pocket_analyze has an incompatible output format; rebuild it with: {BUILD_HINT}"
        )
    return np.asarray(rows, dtype=float).reshape(-1, width)


def accepted(rows: np.ndarray) -> np.ndarray:
    """Detections the engine would sound (drum < noise)."""
    return rows[rows[:, 1] < NOISE] if len(rows) else rows


def model_scale(header: Path = HEADER) -> np.ndarray:
    """``pocket::model::scale`` — the normal-rate neighbour metric's per-feature scale."""
    try:
        text = header.read_text()
    except OSError as exc:
        raise PocketError(f"cannot read {header}: {exc}") from exc
    start = text.find("namespace pocket::model")
    m = re.search(r"scale\s*=\s*\{([^}]*)\}", text[start:]) if start >= 0 else None
    if not m:
        raise PocketError(f"pocket::model::scale not found in {header}")
    vals = np.array([float(v) for v in m.group(1).split(",") if v.strip()], dtype=float)
    if vals.shape != (N_FEATURES,):
        raise PocketError(f"expected {N_FEATURES} scale values, found {vals.shape}")
    return vals


def distance(a: np.ndarray, b: np.ndarray, scale: np.ndarray) -> float:
    d = (np.asarray(a) - np.asarray(b)) / scale
    return float(np.dot(d, d))


def vote(
    features: np.ndarray,
    examples: list[tuple[np.ndarray, int]],
    scale: np.ndarray,
    k: int = 3,
) -> int:
    """Replica of ``Neighbours``: k nearest (ties keep the earlier example), weight 1/(1+d), argmax (first on tie)."""
    ds = [
        (distance(features, ex, scale), i, drum)
        for i, (ex, drum) in enumerate(examples)
    ]
    ds.sort(key=lambda t: (t[0], t[1]))
    votes = np.zeros(max(3, 1 + max((d for _, d in examples), default=0)))
    for d, _, drum in ds[:k]:
        votes[drum] += 1.0 / (1.0 + d)
    return int(np.argmax(votes))


def pick_examples(
    by_drum: dict[int, np.ndarray], scale: np.ndarray, n: int = PROFILE_EXAMPLES
) -> dict[int, np.ndarray]:
    """The ``n`` medoids per drum: hits closest to that drum's centroid in the engine's metric."""
    out: dict[int, np.ndarray] = {}
    for drum in range(3):
        X = np.asarray(by_drum.get(drum, np.zeros((0, N_FEATURES))), dtype=float)
        if len(X) < n:
            raise PocketError(
                f"{DRUMS[drum]}: only {len(X)} accepted hits, need at least {n}"
            )
        Z = X / scale
        c = Z.mean(axis=0)
        order = np.argsort(np.sum((Z - c) ** 2, axis=1), kind="stable")[:n]
        out[drum] = X[order]
    return out


def profile_values(examples: dict[int, np.ndarray]) -> list[float]:
    """The device's v4 profile block: per drum, count then 3 x 17 features (row-major)."""
    vals: list[float] = []
    for drum in range(3):
        X = np.asarray(examples[drum], dtype=float)
        if X.shape != (PROFILE_EXAMPLES, N_FEATURES):
            raise PocketError(
                f"{DRUMS[drum]}: profile needs exactly {PROFILE_EXAMPLES}x{N_FEATURES} values"
            )
        if not np.all(np.isfinite(X)) or np.abs(X).max() > 64:
            raise PocketError(
                f"{DRUMS[drum]}: feature out of the engine's accepted range"
            )
        vals.append(float(PROFILE_EXAMPLES))
        vals.extend(float(v) for v in X.reshape(-1))
    assert len(vals) == PROFILE_VALUES
    return vals


def write_profile(
    examples: dict[int, np.ndarray], meta: dict, path: Path | None = None
) -> dict:
    """Write the profile JSON (canonical copy + a copy on Max's search path for the device button)."""
    path = Path(path) if path is not None else profile_path()
    doc = {
        **meta,
        "version": 4,
        "values": profile_values(examples),
        "examples_per_class": PROFILE_EXAMPLES,
        "features": N_FEATURES,
        "classes": list(DRUMS),
    }
    return write_profile_document(doc, path)


def max_library_dirs() -> list[Path]:
    """Max user Library folders on this machine (resolved at call time; tests stub this)."""
    return sorted(p for p in Path.home().glob(MAX_LIBRARY_GLOB) if p.is_dir())


# v5 extends (and does not replace) the 156-double v4 interchange above.
FEATURE_VERSION = 3
MAX_VOICES = 16
MAX_EXAMPLES = 30
N_LATE = 120
MAX_REJECTS = 30
VOICE_VALUES = 30 + MAX_EXAMPLES * (N_FEATURES + N_LATE // 4)


def medoid_indices(x: np.ndarray, scale: np.ndarray, n: int = 3) -> np.ndarray:
    """Deterministic PAM BUILD + bounded swaps, fitted only to enrollment.

    Unlike three centroid-neighbours, these represent distinct articulations.
    Stable order resolves ties; no improvisation/test features enter selection.
    """
    x = np.asarray(x, dtype=float)
    if len(x) < n or not np.isfinite(x).all():
        raise PocketError(f"need at least {n} finite enrollment hits")
    z = x / scale
    distances = np.maximum(
        0, (z * z).sum(1)[:, None] + (z * z).sum(1)[None, :] - 2 * z @ z.T
    )
    chosen = [int(np.argmin(distances.sum(0)))]
    while len(chosen) < n:
        costs = np.minimum(distances[:, chosen].min(1)[:, None], distances).sum(0)
        costs[chosen] = np.inf
        chosen.append(int(np.argmin(costs)))
    for _ in range(8 if n <= 3 else 0):
        cost = distances[:, chosen].min(1).sum()
        best = None
        for slot in range(n):
            for candidate in range(len(x)):
                if candidate in chosen:
                    continue
                trial = chosen.copy()
                trial[slot] = candidate
                loss = distances[:, trial].min(1).sum()
                if loss < cost - 1e-9:
                    cost, best = loss, trial
        if best is None:
            break
        chosen = best
    return np.asarray(chosen)


def build_profile(
    voices: list[dict], sr: int, negatives: np.ndarray | None = None
) -> dict:
    """Fit a bounded v5 profile from per-class native enrollment rows.

    rows = onset, factory label, velocity, decision time, early17, late120,
    observed slice lengths(3), observed window end. Labels come from the clip.
    An early radius is calibrated from every enrollment hit, not the test set.
    """
    if not 2 <= len(voices) <= MAX_VOICES or sr not in (
        8000,
        16000,
        22050,
        32000,
        44100,
        48000,
        88200,
        96000,
        176400,
        192000,
    ):
        raise PocketError("profile needs 2–16 voices and a supported sample rate")
    names = [v["name"] for v in voices]
    if len(set(names)) != len(names) or any(
        not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", name) for name in names
    ):
        raise PocketError(
            "voice names must be unique, lowercase ASCII, at most 24 characters"
        )
    prepared = []
    for v in voices:
        rows = np.asarray(v["rows"], dtype=float)
        if (
            rows.ndim != 2
            or rows.shape[1] != 145
            or len(rows) < 3
            or not np.isfinite(rows).all()
        ):
            raise PocketError(
                f"{v['name']}: need at least 3 finite native enrollment rows"
            )
        # Only isolated, complete windows train the delayed model. Short rolls
        # still train the early classifier, but never pretend to be 80 ms hits.
        expected = np.round(np.array([0.010, 0.030, 0.040]) * sr).astype(int)
        full = rows[np.all(rows[:, 141:144] == expected, axis=1)]
        if len(full) < 3:
            raise PocketError(
                f"{v['name']}: need 3 isolated hits, preferably 30; leave 200 ms between sounds"
            )
        if (
            isinstance(v["note"], bool)
            or int(v["note"]) != v["note"]
            or not 0 <= v["note"] <= 127
        ):
            raise PocketError(f"{v['name']}: invalid MIDI note")
        prepared.append((v, rows, full))
    # Standardization belongs to this enrollment, never the held-out performer/take.
    cloud = np.concatenate([f[:, 21:141] for _, _, f in prepared])
    scale = np.maximum(cloud.std(axis=0), 1e-3)
    center = cloud.mean(axis=0)
    early_scale = model_scale()
    blocks = []
    meta = []
    for v, rows, full in prepared:
        early = rows[:, 4:21]
        ne = min(MAX_EXAMPLES, len(early))
        picked = (
            early if ne == len(early) else early[medoid_indices(early, early_scale, ne)]
        )
        late = full[:, 21:141]
        nl = min(MAX_EXAMPLES, len(late))
        kept = late if nl == len(late) else late[medoid_indices(late, scale, nl)]
        nearest = (
            (((early[:, None, :] - picked[None, :, :]) / early_scale) ** 2)
            .sum(2)
            .min(1)
        )
        # Entire training support plus a fixed 50% margin. Explicit negatives
        # are essential when a breath/syllable lies inside a positive cloud.
        # Leave-one-out nearest positive distance calibrates a radius even
        # when all enrollment hits fit in the bank (self-distance is zero).
        pairwise = (((early[:, None, :] - early[None, :, :]) / early_scale) ** 2).sum(2)
        np.fill_diagonal(pairwise, np.inf)
        radius = max(1.0, float(np.maximum(nearest, pairwise.min(1)).max()) * 1.5)
        name = list(v["name"].encode("ascii")) + [0] * (24 - len(v["name"]))
        early_pad = np.zeros((MAX_EXAMPLES, 17))
        early_pad[:ne] = picked
        quantized = np.zeros((MAX_EXAMPLES, 120), dtype=np.int8)
        quantized[:nl] = np.clip(
            np.rint((kept - center) / scale * 16), -128, 127
        ).astype(np.int8)
        b = quantized.view(np.uint8).astype(np.uint32).reshape(-1, 4)
        packed = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16 | b[:, 3] << 24
        blocks.extend(
            [int(v["note"]), *name, ne, nl, radius, 0, 0, *early_pad.ravel(), *packed]
        )
        meta.append(
            {
                "name": v["name"],
                "note": int(v["note"]),
                "hits": len(rows),
                "full_windows": len(full),
                "radius": radius,
            }
        )
    negative_rows = (
        np.empty((0, 145)) if negatives is None else np.asarray(negatives, dtype=float)
    )
    if (
        negative_rows.ndim != 2
        or negative_rows.shape[1] not in (17, 145)
        or not np.isfinite(negative_rows).all()
    ):
        raise PocketError(
            "negative enrollment needs native rows or 17-feature legacy examples"
        )
    negative = (
        negative_rows[:, 4:21] if negative_rows.shape[1] == 145 else negative_rows
    )
    if len(negative) > MAX_REJECTS:
        negative = negative[medoid_indices(negative, early_scale, MAX_REJECTS)]
    if len(negative) and np.abs(negative).max() > 64:
        raise PocketError("negative enrollment contains invalid features")
    negative_late = np.empty((0, 120))
    masks = np.empty(0, dtype=int)
    if negative_rows.shape[1] == 145 and len(negative_rows):
        expected = np.round(np.array([0.010, 0.030, 0.040]) * sr).astype(int)
        # Only sustained negative attacks teach the immediate veto. Short
        # voice fragments can be acoustically indistinguishable from kicks.
        usable = negative_rows[np.all(negative_rows[:, 141:144] >= expected, axis=1)]
        masks = (usable[:, 141:144] >= expected).astype(int) @ np.array([1, 2, 4])
        negative_late = usable[:, 21:141]
        if len(negative_late) > MAX_REJECTS:
            selection = medoid_indices(negative_late, scale, MAX_REJECTS)
            negative_late, masks = negative_late[selection], masks[selection]
    quantized = np.clip(
        np.rint((negative_late - center) / scale * 16), -128, 127
    ).astype(np.int8)
    b = quantized.view(np.uint8).astype(np.uint32).reshape(-1, 4)
    packed_negative = (b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16 | b[:, 3] << 24).reshape(
        -1, 30
    )
    packed_negative = np.column_stack([masks, packed_negative]).ravel()
    values = [
        5,
        FEATURE_VERSION,
        len(voices),
        len(negative),
        sr,
        len(negative_late),
        *scale,
        *center,
        *blocks,
        *negative.ravel(),
        *packed_negative,
    ]
    if not np.isfinite(values).all() or any(
        np.abs(v) > 1024 for _, _, rows in prepared for v in rows[:, 21:141].ravel()
    ):
        raise PocketError("features exceed the native profile range")
    return {
        "version": 5,
        "feature_version": FEATURE_VERSION,
        "sample_rate": sr,
        "values": [float(v) for v in values],
        "voices": meta,
        "reject_examples": len(negative),
        "commit_reject_examples": len(negative_late),
        "examples_per_class": MAX_EXAMPLES,
        "features": 17,
        "commit_features": 120,
        "commit_quantization": "signed q4, four bytes per uint32 double",
        "pick": "pam-enrollment-only",
    }


def write_profile_document(doc: dict, path: Path | None = None) -> dict:
    """Atomic replacement keeps a PROFILE click from observing half a JSON file."""
    from mouthflow.enroll import _atomic_write

    path = Path(path) if path is not None else profile_path()
    destinations = [path, *(lib / MAX_PROFILE_NAME for lib in max_library_dirs())]
    text = json.dumps(doc, allow_nan=False)
    for destination in destinations:
        _atomic_write(destination, text)
    return {**doc, "written": [str(p) for p in destinations]}


def predict_profile(doc: dict, rows: np.ndarray) -> np.ndarray:
    """Exact v5 native metric replica, including quantization and open-set rejection.

    Both outputs use -1 for rejection. The late decision never promotes an
    early rejected onset; no drum can appear after being silent live.
    """
    v = np.asarray(doc["values"], dtype=float)
    if v[0] != 5 or v[1] != FEATURE_VERSION:
        raise PocketError("predict_profile needs a v5 profile")
    nvoices, nrejects, sr = int(v[2]), int(v[3]), v[4]
    scale, center = v[6:126], v[126:246]
    early, late, radii = [], [], []
    at = 246
    for d in range(nvoices):
        block = v[at : at + VOICE_VALUES]
        at += VOICE_VALUES
        ne, nl = int(block[25]), int(block[26])
        radii.append(block[27])
        ex = block[30 : 30 + MAX_EXAMPLES * 17].reshape(MAX_EXAMPLES, 17)
        early.extend((f, d) for f in ex[:ne])
        packed = block[30 + MAX_EXAMPLES * 17 :].astype(np.uint32)
        b = np.stack([(packed >> (8 * k)) & 255 for k in range(4)], axis=1).astype(
            np.uint8
        )
        decoded = b.reshape(MAX_EXAMPLES, 120).view(np.int8).astype(float) / 16
        late.extend((f, d) for f in decoded[:nl])
    negatives = v[at : at + nrejects * 17].reshape(nrejects, 17)
    negative_blocks = v[at + nrejects * 17 :].reshape(int(v[5]), 31)
    negative_masks = negative_blocks[:, 0].astype(int)
    packed = negative_blocks[:, 1:].ravel().astype(np.uint32)
    b = np.stack([(packed >> (8 * k)) & 255 for k in range(4)], axis=1).astype(np.uint8)
    negative_late = b.reshape(int(v[5]), 120).view(np.int8).astype(float) / 16
    early_scale = model_scale()
    out = []
    expected = np.round(np.array([0.010, 0.030, 0.040]) * sr)
    for row in rows:
        f = row[4:21]
        live = vote(f, early, early_scale)
        nearest = min(distance(f, ex, early_scale) for ex, d in early if d == live)
        noise_vote = sorted(
            [
                (distance(f, ex, early_scale), i, False)
                for i, (ex, _) in enumerate(early)
            ]
            + [
                (distance(f, ex, early_scale), len(early) + i, True)
                for i, ex in enumerate(negatives)
            ]
        )
        if len(negative_late) >= 4:
            rich = (row[21:61] - center[:40]) / scale[:40]
            noise_vote = sorted(
                [
                    (float(np.sum((rich - ex[:40]) ** 2)), i, False)
                    for i, (ex, _) in enumerate(late)
                ]
                + [
                    (float(np.sum((rich - ex[:40]) ** 2)), len(late) + i, True)
                    for i, ex in enumerate(negative_late)
                    if negative_masks[i] == 7
                ]
            )
        if (radii[live] > 0 and nearest > radii[live]) or sum(
            noise for _, _, noise in noise_vote[:5]
        ) >= 4:
            out.append((-1, -1))
            continue
        commit = live
        if row[142] >= expected[1] and late:
            mask = np.repeat(row[141:144] >= expected, 40)
            f = (row[21:141] - center) / scale
            ds = sorted(
                (float(np.sum((f[mask] - ex[mask]) ** 2)), i, d)
                for i, (ex, d) in enumerate(late)
            )
            votes = np.zeros(nvoices)
            for _, _, d in ds[:5]:
                votes[d] += 1
            commit = int(np.argmax(votes))
            gate = sorted(
                [(distance, i, False) for distance, i, d in ds]
                + [
                    (float(np.sum((f[mask] - ex[mask]) ** 2)), len(late) + i, True)
                    for i, ex in enumerate(negative_late)
                    if negative_masks[i] & (7 if mask[-1] else 3)
                    == (7 if mask[-1] else 3)
                ]
            )
            if sum(noise for _, _, noise in gate[:5]) >= 4:
                commit = -1
        out.append((live, commit))
    return np.asarray(out, dtype=int).reshape(-1, 2)
