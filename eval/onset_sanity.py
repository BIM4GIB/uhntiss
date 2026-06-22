"""Tempo-independent onset-detector sanity check.

The headline ``run_eval`` onset F1 is measured on *quantised* hit times, so a
tempo/phase error folds into it — a perfect detector can still score badly.
This harness isolates the detector: it scores the RAW (un-quantised) onset
times from ``transcribe._detect_onsets`` against the exact mimic reference
grids (``mimic/<name>.grid.json``), aligning the grid to the performance with
the same reaction-offset search the mimic harness uses for auto-labelling.

Use it as the regression guard when changing onset detection — it updates in
seconds and tells you whether the *detector* improved, independent of tempo.

Run: ``uv run python -m eval.onset_sanity`` from the repo root.
"""

from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np

from mimic.take import _match  # reuse the reaction-offset matcher
from mouthflow import transcribe as T

REPO_ROOT = Path(__file__).resolve().parent.parent
MIMIC_DIR = REPO_ROOT / "mimic"
RMS_FLOOR = 0.005  # same gate the mimic auto-labeller uses


def _score_take(grid_path: Path) -> tuple[str, float, float, float, int, float] | None:
    name = grid_path.name.replace(".grid.json", "")
    wav = grid_path.with_name(f"{name}.mimic.wav")
    if not wav.exists():
        return None

    grid = json.loads(grid_path.read_text())["grid"]
    y, _ = librosa.load(str(wav), sr=T._SR, mono=True)
    onsets = [t for t in T._detect_onsets(y, T._SR) if T._features_at(y, T._SR, t)["rms"] >= RMS_FLOOR]

    gt = np.array([t for t, _ in grid])
    # Best reaction offset = the one maximising onset/grid matches (narrow
    # window so it can't lock onto an off-by-one-slot alignment).
    best = max(np.arange(0.12, 0.30, 0.005), key=lambda d: len(_match(onsets, gt, d)))
    tp = len(_match(onsets, gt, best))
    fp = len(onsets) - tp
    fn = len(grid) - tp
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return name, p, r, f1, len(grid), best


def main() -> int:
    grids = sorted(MIMIC_DIR.glob("*.grid.json"))
    if not grids:
        print(f"No reference grids in {MIMIC_DIR}. Run `mimic.take gen` + `rec` first.")
        return 1

    print("ONSET DETECTOR SANITY (raw onsets vs reference grid, tempo-independent)")
    print("-" * 70)
    print(f"  {'take':<16} {'P':>4} {'R':>4} {'F1':>5} {'notes':>6} {'offset':>7}")
    rows = [r for g in grids if (r := _score_take(g))]
    for name, p, r, f1, n, best in rows:
        print(f"  {name:<16} {p:>4.2f} {r:>4.2f} {f1:>5.2f} {n:>6} {best * 1000:>5.0f}ms")
    if rows:
        macro = sum(r[3] for r in rows) / len(rows)
        print("-" * 70)
        print(f"  macro F1 over {len(rows)} take(s): {macro:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
