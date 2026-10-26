"""Bounded Pocket DSP stress: max enrollment banks and dense loop playback.

Synthetic features/audio stress storage and compute, not classifier accuracy.
No corpus, held-out recording or fitted threshold enters this benchmark.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from mouthflow import pocket


def run(binary: Path, sr: int) -> dict:
    def rows(voice):
        rng = np.random.default_rng(voice + 10)
        r = np.zeros((30, 145))
        r[:, 0] = np.arange(30) * 0.4
        r[:, 2] = 0.6
        r[:, 3] = r[:, 0] + 0.01
        r[:, 4:21] = rng.normal(voice * 2, 0.1, (30, 17))
        r[:, 21:141] = rng.normal(voice * 2, 0.1, (30, 120))
        r[:, 141:144] = np.round(np.array([0.01, 0.03, 0.04]) * sr)
        r[:, 144] = r[:, 0] + 0.08
        return r

    voices = [dict(name=f"voice_{d}", note=36 + d, rows=rows(d)) for d in range(16)]
    noise = rows(0)
    noise[:, 4:141] -= 30
    doc = pocket.build_profile(voices, sr, noise)
    # Disable support radii only in this synthetic stress profile so every
    # pulse exercises the full live/commit path, not an early rejection.
    for i in range(16):
        doc["values"][246 + i * pocket.VOICE_VALUES + 27] = 0
    t = np.arange(sr * 10) / sr
    within = np.where(t % 2 < 1, t % 0.05, t % 0.2)
    y = np.where(
        within < 0.025, 0.7 * np.sin(within * 2 * np.pi * 95) * np.exp(-within * 60), 0
    ).astype("<f4")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "profile.txt"
        path.write_text(" ".join(map(str, doc["values"])))
        p = subprocess.run(
            [str(binary), str(sr), str(path)],
            input=y.tobytes(),
            capture_output=True,
            check=True,
        )
    return {
        **json.loads(p.stdout),
        "sample_rate": sr,
        "profile": "16 voices x30 +30 negatives; 512-event playback during capture",
        "input": "alternating seconds of 50 ms rolls and 200 ms spaced pulses",
        "limits": "Core CPU timing on this machine; no host scheduling or round-trip latency measurement.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=pocket.BINARY.with_name("pocket_bench")
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = [run(args.binary, sr) for sr in (48000, 96000)]
    text = json.dumps(results, indent=2) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
