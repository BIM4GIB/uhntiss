"""Integration checks for the shipping native binary, using held-out vocal hits.

Requires pocket_analyze and pocket_stream from the CMake build. This is a
constructed performance, not evidence of unseen-performer/stage accuracy.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
from eval.native_auto import ROOT, analyze, read


def groove(sr, gain=1.0, dense=True, noise=0.0):
    info = json.loads((ROOT / "native/pocket/auto-model.json").read_text())
    clips = {d: [] for d in range(3)}
    for r in info["heldout_attacks"]:
        y, source_sr = read(ROOT / r["source"])
        start = round((r["time"] - 0.012) * source_sr)
        duration = 0.115 if dense else 0.18
        v = y[start : start + round(duration * source_sr)].copy()
        n = round(0.01 * source_sr)
        v[-n:] *= np.linspace(1, 0, n)
        n = round(0.002 * source_sr)
        v[:n] *= np.linspace(0, 1, n)
        clips[r["drum"]].append(
            (resample_poly(v, sr, source_sr), r["time"] - start / source_sr)
        )
    pattern = [0, 2, 2, 1, 2, 0, 2, 1, 0, 2, 1, 2, 0, 2, 2, 1] * (2 if dense else 1)
    offsets = [
        0,
        0.009,
        -0.013,
        0.024,
        -0.006,
        0.017,
        -0.011,
        0.028,
        -0.009,
        0.013,
        0.003,
        0.031,
        -0.014,
        0.022,
        0.005,
        0.019,
    ]
    y = np.random.default_rng(8472).normal(0, noise, round(sr * 4.8)).astype("float32")
    refs = []
    used = [0, 0, 0]
    for i, d in enumerate(pattern):
        v, onset = clips[d][used[d] % len(clips[d])]
        used[d] += 1
        t = (
            0.08
            + i * (0.14 if dense else 0.285)
            + offsets[i % 16] * (0.5 if dense else 1)
        )
        start = round(t * sr)
        y[start : start + len(v)] += v * gain
        refs.append((t + onset, d))
    return y, refs


def check(analyzer, stream):
    info = json.loads((ROOT / "native/pocket/auto-model.json").read_text())
    for source in info["training"]:
        assert (
            hashlib.sha256((ROOT / source["source"]).read_bytes()).hexdigest()
            == source["sha256"]
        ), "calibration source changed; regenerate model and evidence"
    assert (
        hashlib.sha256(
            (ROOT / "native/pocket/include/pocket/attack_model.hpp").read_bytes()
        ).hexdigest()
        == info["compiled_model_sha256"]
    ), "compiled model/provenance mismatch"
    assert (
        hashlib.sha256(
            (ROOT / "native/pocket/include/pocket/transient_model.hpp").read_bytes()
        ).hexdigest()
        == info["transient_model_sha256"]
    ), "compiled transient model/provenance mismatch"
    assert "live_6_uptempo" not in info["transient"]["training_groups"]
    cases = []
    for sr in (44100, 48000, 96000):
        for gain in (0.5, 1.0, 2.0):
            y, refs = groove(sr, gain)
            rows = analyze(analyzer, y, sr)
            assert len(rows) == len(refs), (
                sr,
                gain,
                "extra/missing attacks",
                len(rows),
                len(refs),
            )
            # Sorted, one-to-one comparison, never a greedy reuse of a hit.
            assert [int(r[1]) for r in rows] == [d for _, d in refs], (
                sr,
                gain,
                "wrong voice",
            )
            errors = [abs(r[0] - t) * 1000 for r, (t, _) in zip(rows, refs)]
            assert max(errors) < 25, (sr, gain, "source attack association", errors)
            assert np.all((rows[:, 3] - rows[:, 0]) <= 0.010001)
            # Every detection must become exactly one unsnapped loop event.
            with tempfile.TemporaryDirectory() as tmp:
                raw = Path(tmp) / "output.f32"
                p = subprocess.run(
                    [str(stream), str(sr), "100", str(raw)],
                    input=y.astype("<f4").tobytes(),
                    capture_output=True,
                    check=True,
                )
                events = np.array(
                    [
                        list(map(float, line.split(",")))
                        for line in p.stdout.decode().splitlines()
                    ]
                )
                assert len(events) == len(rows), "capture lost or duplicated an attack"
                assert np.array_equal(events[:, 1], rows[:, 1]), "loop changed a voice"
                assert np.max(abs(events[:, 0] * 0.6 - rows[:, 0])) < 1 / sr, (
                    "pocket moved"
                )
                assert np.max(abs(events[:, 2] - rows[:, 2])) < 1e-6, "dynamics changed"
                out = np.fromfile(raw, dtype="<f4").reshape(-1, 2)
                assert np.isfinite(out).all() and np.abs(out).max() <= 1, (
                    "invalid audio"
                )
                first = np.flatnonzero(np.abs(out[:, 0]) > 1e-7)[0] / sr
                assert first - rows[0, 0] < 0.012, (
                    "live path exceeded algorithmic latency bound"
                )
            cases.append(
                dict(
                    sample_rate=sr,
                    gain=gain,
                    hits=len(rows),
                    correct_voices=len(rows),
                    max_source_alignment_ms=max(errors),
                    capture_error_samples_less_than=1,
                )
            )
    y, _ = groove(48000, 1.0, noise=0.0005)
    rows = analyze(analyzer, y, 48000)
    assert len(rows) == 32 and [int(r[1]) for r in rows] == [
        d for _, d in groove(48000)[1]
    ], "quiet room noise changed groove"
    for y in (
        np.zeros(48000),
        np.ones(48000) * 0.001,
        np.random.default_rng(1).normal(0, 0.0005, 48000),
    ):
        assert len(analyze(analyzer, y, 48000)) == 0, "room noise triggered drums"
    return dict(
        reference="Constructed unsnapped grooves from reserved source attacks; all gain variants of those attacks excluded from training. Same recording session, not unseen performers. Source-onset association uses 25ms; captured timestamps independently checked within one sample.",
        cases=cases,
        room_noise="pass",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build", type=Path, default=ROOT / "native/pocket/build")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    r = check(
        (args.build / "pocket_analyze").resolve(),
        (args.build / "pocket_stream").resolve(),
    )
    s = json.dumps(r, indent=2) + "\n"
    if args.report:
        args.report.write_text(s)
    print(s)


if __name__ == "__main__":
    main()
