"""Bridge to the native Pocket drum engine (``native/pocket``): the other device line.

Pocket is the realtime C++ instrument (PR #19). It classifies attacks with
its own forests over a 17-feature, 10 ms causal window and personalises with
a **Teach profile**: 3 examples per voice, nearest-neighbour vote (see
``Profile`` / ``Neighbours`` in ``native/pocket/src/engine.cpp``). This
module is the only place the Python side knows about those contracts:

- ``analyze``  runs the research tool ``pocket_analyze`` (stdin PCM -> one
  CSV row per detection: beat, drum, velocity, time, 17 features) — the same
  detector + features the device uses.
- ``model_scale`` reads the per-feature scale the engine's neighbour metric
  uses (``pocket::model::scale`` in ``attack_model.hpp``) so Python can
  replicate ``Profile::classify`` exactly (``vote``).
- ``pick_examples`` chooses the 3 most representative hits per voice
  (medoids in that metric) from an enrollment clip — better than the three
  you happened to play first at the mic.
- ``profile_values`` lays them out as the device's v4 profile block
  (``pocket::profileValues`` = 3 x (1 + 3 x 17) = 156 doubles), which the
  external accepts as a ``profile`` message and persists with the Set.

Pocket owns the feature definition and the metric; if either changes, this
module must change with it. ``tests/test_pocket.py`` pins the replica.
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
PROFILE_EXAMPLES = 3  # fixed storage in the engine: Profile::examples[3][3]
PROFILE_VALUES = 3 * (1 + PROFILE_EXAMPLES * N_FEATURES)  # == pocket::profileValues
STATE_VERSION = 4

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
        raise PocketError(f"pocket_analyze not found at {p}; build it with: {BUILD_HINT}")
    return p


def analyze(binary: Path, samples: np.ndarray, sr: int, gate: float = 0.010, policy: str | None = None) -> np.ndarray:
    """Run Pocket's detector over ``samples`` (mono float). Rows: beat, drum, velocity, time, f1..f17."""
    args = [str(binary), str(sr), str(gate)] + ([policy] if policy else [])
    p = subprocess.run(
        args, input=np.asarray(samples, dtype="<f4").tobytes(), capture_output=True, check=True
    )
    rows = [list(map(float, line.split(","))) for line in p.stdout.decode().splitlines() if line.strip()]
    width = 4 + N_FEATURES + (6 if policy == "transient" else 0)
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


def vote(features: np.ndarray, examples: list[tuple[np.ndarray, int]], scale: np.ndarray, k: int = 3) -> int:
    """Replica of ``Neighbours``: k nearest (ties keep the earlier example), weight 1/(1+d), argmax (first on tie)."""
    ds = [(distance(features, ex, scale), i, drum) for i, (ex, drum) in enumerate(examples)]
    ds.sort(key=lambda t: (t[0], t[1]))
    votes = np.zeros(3)
    for d, _, drum in ds[:k]:
        votes[drum] += 1.0 / (1.0 + d)
    return int(np.argmax(votes))


def pick_examples(by_drum: dict[int, np.ndarray], scale: np.ndarray, n: int = PROFILE_EXAMPLES) -> dict[int, np.ndarray]:
    """The ``n`` medoids per drum: hits closest to that drum's centroid in the engine's metric."""
    out: dict[int, np.ndarray] = {}
    for drum in range(3):
        X = np.asarray(by_drum.get(drum, np.zeros((0, N_FEATURES))), dtype=float)
        if len(X) < n:
            raise PocketError(f"{DRUMS[drum]}: only {len(X)} accepted hits, need at least {n}")
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
            raise PocketError(f"{DRUMS[drum]}: profile needs exactly {PROFILE_EXAMPLES}x{N_FEATURES} values")
        if not np.all(np.isfinite(X)) or np.abs(X).max() > 64:
            raise PocketError(f"{DRUMS[drum]}: feature out of the engine's accepted range")
        vals.append(float(PROFILE_EXAMPLES))
        vals.extend(float(v) for v in X.reshape(-1))
    assert len(vals) == PROFILE_VALUES
    return vals


def write_profile(examples: dict[int, np.ndarray], meta: dict, path: Path | None = None) -> dict:
    """Write the profile JSON (canonical copy + a copy on Max's search path for the device button)."""
    path = Path(path) if path is not None else profile_path()
    doc = {
        "version": STATE_VERSION,
        "values": profile_values(examples),
        "examples_per_class": PROFILE_EXAMPLES,
        "features": N_FEATURES,
        "classes": list(DRUMS),
        **meta,
    }
    text = json.dumps(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    copies = []
    for lib in max_library_dirs():
        (lib / MAX_PROFILE_NAME).write_text(text)
        copies.append(str(lib / MAX_PROFILE_NAME))
    doc["written"] = [str(path), *copies]
    return doc


def max_library_dirs() -> list[Path]:
    """Max user Library folders on this machine (resolved at call time; tests stub this)."""
    return sorted(p for p in Path.home().glob(MAX_LIBRARY_GLOB) if p.is_dir())
