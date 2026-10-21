"""Native full-take regression, causality and unsnapped capture checks."""

from __future__ import annotations
import argparse
import json
import subprocess
import tempfile
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
from eval.native_auto import ROOT, analyze, read
from eval.real_auto import load, score, summarize, report


def check(build):
    binary = build / "pocket_analyze"
    audit = load("pocket-real-takes.json")
    result = report(binary)
    baseline = load("pocket05-deployment-baseline.json")
    assert result["after"]["summary"] == baseline["original_summary"], (
        "original full-take output changed"
    )
    cases = []
    for sr in (44100, 48000, 96000):
        for gain in (0.25, 0.5, 1.0, 2.0):
            scores = []
            for take in audit["takes"]:
                y, source_sr = read(ROOT / take["source"])
                y = resample_poly(y, sr, source_sr) * gain
                rows = analyze(binary, y, sr)
                assert np.all((rows[:, 3] - rows[:, 0]) <= 0.010001), (
                    "decision window increased"
                )
                scores.append(score(rows, take))
            cases.append(
                dict(sample_rate=sr, gain=gain, summary=summarize(scores), takes=scores)
            )
    for before, after in zip(baseline["cases"], cases):
        assert (before["sample_rate"], before["gain"]) == (
            after["sample_rate"],
            after["gain"],
        )
        assert after["summary"]["correct"] >= before["summary"]["correct"], (
            "whole-take correct voices regressed"
        )
        for metric in ("missed", "wrong", "extra"):
            assert after["summary"][metric] <= before["summary"][metric], (
                f"whole-take {metric} regressed"
            )
    # Exercise late portions of the full takes with their preceding audio
    # intact. Comparing only the first two bars would miss most of the playing.
    capture_checks = []
    with tempfile.TemporaryDirectory() as tmp:
        for take in audit["takes"]:
            y, sr = read(ROOT / take["source"])
            rows = analyze(binary, y, sr)
            for start in np.arange(0, len(y) / sr, 4.8):
                raw = Path(tmp) / "audio.f32"
                p = subprocess.run(
                    [
                        str(build / "pocket_stream"),
                        str(sr),
                        "100",
                        str(raw),
                        str(start / 0.6),
                    ],
                    input=y.astype("<f4").tobytes(),
                    capture_output=True,
                    check=True,
                )
                events = np.array(
                    [
                        list(map(float, line.split(",")))
                        for line in p.stdout.decode().splitlines()
                    ]
                ).reshape(-1, 3)
                expected = rows[(rows[:, 0] >= start) & (rows[:, 0] < start + 4.8)]
                assert len(events) == len(expected), (
                    take["name"],
                    start,
                    "lost or duplicated capture",
                    len(events),
                    len(expected),
                )
                if len(events):
                    assert np.array_equal(events[:, 1], expected[:, 1]), (
                        "captured voice changed"
                    )
                    delta = (
                        np.max(abs((events[:, 0] * 0.6 + start) - expected[:, 0])) * sr
                    )
                    assert delta < 1.01, (take["name"], start, "pocket moved", delta)
                    assert np.max(abs(events[:, 2] - expected[:, 2])) < 1e-6, (
                        "captured dynamics changed"
                    )
                else:
                    delta = 0.0
                audio = np.fromfile(raw, dtype="<f4")
                assert np.isfinite(audio).all() and abs(audio).max() <= 1, (
                    "invalid output"
                )
                capture_checks.append(
                    dict(
                        take=take["name"],
                        start_seconds=start,
                        hits=len(events),
                        max_error_samples=delta,
                    )
                )
            # Changing everything after the cut must leave completed decisions
            # and their onset timestamps before the cut bit-for-bit identical.
            cut = len(y) // 2
            changed = y.copy()
            changed[cut:] = np.random.default_rng(3).normal(0, 0.1, len(y) - cut)
            altered = analyze(binary, changed, sr, include_rejected=True)
            original = analyze(binary, y, sr, include_rejected=True)
            assert np.array_equal(
                original[original[:, 3] < cut / sr], altered[altered[:, 3] < cut / sr]
            ), "future samples affected earlier decisions"
    result.update(
        rate_gain_cases=cases, capture_windows=capture_checks, causality="pass"
    )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    result = check(a.build.resolve())
    a.report.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                before=result["before"]["summary"],
                after=result["after"]["summary"],
                variants=[
                    {k: v for k, v in c.items() if k != "takes"}
                    for c in result["rate_gain_cases"]
                ],
                capture_windows=len(result["capture_windows"]),
                causality=result["causality"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
