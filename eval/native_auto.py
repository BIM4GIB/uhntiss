"""Native analysis bridge and legacy weak-grid diagnostic.

Current model training and full-take evaluation live in eval.real_auto.
The old fixed-grid report is retained only for historical comparison.
Python is never loaded by the device.
"""

from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("kick", "snare", "hat")
GAINS = (0.5, 1.0, 2.0)


def analyze(
    binary,
    samples,
    sr,
    gate=0.010,
    *,
    include_rejected=False,
    candidates=False,
    transient=False,
):
    p = subprocess.run(
        [str(binary), str(sr), str(gate)]
        + (["transient"] if transient else ["candidates"] if candidates else []),
        input=np.asarray(samples, dtype="<f4").tobytes(),
        capture_output=True,
        check=True,
    )
    rows = [list(map(float, row.split(","))) for row in p.stdout.decode().splitlines()]
    result = np.asarray(rows).reshape(
        -1, len(rows[0]) if rows else 27 if transient else 21
    )
    return result if include_rejected else result[result[:, 1] < 3]


def read(path):
    y, sr = sf.read(path, dtype="float32")
    return (y.mean(1) if y.ndim > 1 else y), sr


def train(binary):
    from eval.real_auto import train as train_current

    train_current(binary)


def match(pred, refs, tolerance=0.075):
    pairs = []
    used = set()
    other = set()
    candidates = sorted(
        (abs(t - r["t"]), i, j)
        for i, t in enumerate(pred)
        for j, r in enumerate(refs)
        if abs(t - r["t"]) < tolerance
    )
    for _, i, j in candidates:
        if i not in used and j not in other:
            pairs.append((i, j))
            used.add(i)
            other.add(j)
    return pairs


def report(binary):
    from eval.featurelab import labeled_onsets

    refs = labeled_onsets()
    clips = []
    for path in sorted((ROOT / "mimic").glob("*.mimic.wav")):
        y, sr = read(path)
        rows = analyze(binary, y, sr)
        take = path.name.replace(".mimic.wav", "")
        r = [v for v in refs if v["take"] == take]
        pairs = match(rows[:, 0], r)
        clips.append(
            dict(
                take=take,
                detected=len(rows),
                matched=len(pairs),
                unmatched_detections=len(rows) - len(pairs),
                unmatched_weak_labels=len(r) - len(pairs),
                matched_class_accuracy=sum(
                    int(rows[i, 1]) == NAMES.index(r[j]["label"]) for i, j in pairs
                )
                / max(1, len(pairs)),
            )
        )
    return dict(
        reference="Legacy auto-aligned labels; several grids drift. Not a current accuracy benchmark; use eval.real_auto report.",
        clips=clips,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("train", "report"))
    p.add_argument(
        "--binary", type=Path, default=ROOT / "native/pocket/build/pocket_analyze"
    )
    args = p.parse_args()
    if args.command == "train":
        train(args.binary.resolve())
    else:
        print(json.dumps(report(args.binary.resolve()), indent=2))


if __name__ == "__main__":
    main()
