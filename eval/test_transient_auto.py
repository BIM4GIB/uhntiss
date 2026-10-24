"""Frozen breath-shaped noise controls; these signals never enter training.

Smooth filtered noise is a stress proxy, not a human-breath benchmark. Pocket04
already triggers on some controls. A release must not add new triggers to them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

from eval.native_auto import ROOT, analyze, match


def controls():
    sr = 48000
    rng = np.random.default_rng(85051)
    for band in ((80, 800), (300, 4000), (2500, 12000)):
        for ramp in (0.02, 0.05, 0.1):
            audio = sosfilt(
                butter(2, band, fs=sr, btype="bandpass", output="sos"),
                rng.normal(size=sr * 2),
            )
            audio /= max(np.std(audio), 1e-9)
            envelope = np.zeros(sr * 2)
            start, rise, hold = round(0.2 * sr), round(ramp * sr), round(0.3 * sr)
            envelope[start : start + rise] = np.linspace(0, 1, rise) ** 2
            envelope[start + rise : start + rise + hold] = 1
            envelope[start + rise + hold : start + 2 * rise + hold] = (
                np.linspace(1, 0, rise) ** 2
            )
            for gain in (0.003, 0.006, 0.012):
                yield (
                    dict(band=list(band), ramp=ramp, gain=gain),
                    audio * envelope * gain,
                )


def check(binary):
    baseline = json.loads(
        (ROOT / "eval/data/pocket04-breath-controls.json").read_text()
    )
    cases = []
    for before, (settings, audio) in zip(baseline["cases"], controls(), strict=True):
        assert before["settings"] == settings
        rows = analyze(binary, audio, 48000)
        pairs = match(rows[:, 0], [dict(t=e[0]) for e in before["events"]], 0.025)
        assert len(pairs) == len(rows), (settings, "new noise triggers")
        cases.append(
            dict(
                settings=settings,
                before=len(before["events"]),
                after=len(rows),
                events=rows[:, :4].tolist(),
            )
        )
    return dict(
        scope="Synthetic breath-shaped noise, never training data. No added triggers relative to Pocket04; not a claim of zero breath false positives.",
        cases=cases,
        before=sum(c["before"] for c in cases),
        after=sum(c["after"] for c in cases),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = check(args.binary.resolve())
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    main()
