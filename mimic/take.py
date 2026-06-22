"""Parameterised mimic-a-beat harness.

Subcommands (all keyed by --name):
  gen   synth a reference loop (count-in + preset pattern) + ground-truth grid
  rec   sounddevice.playrec: play reference to headphones, record the mimic
        sample-aligned (so the grid IS ground truth)
  score reaction-offset search -> auto-label -> model-vs-heuristic accuracy,
        dump labelled features (for retraining), emit a corpus clip

Usage:
  uv run python -m mimic.take gen --name bb100 --bpm 100 --preset boombap
  uv run python -m mimic.take rec --name bb100
  uv run python -m mimic.take score --name bb100 --clip 02_bb100
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

import librosa
import mido
import numpy as np
import soundfile as sf

from mouthflow import transcribe as T

SR = 44100
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LAB2PITCH = {"kick": 36, "snare": 38, "hat": 42}
PITCH2LAB = {36: "kick", 38: "snare", 42: "hat", 46: "hat", 39: "perc", -1: "DROP"}

# one sound per 8th slot so it's mimicable by mouth; beats within a 4/4 bar
PRESETS = {
    "boombap": {"kick": [0, 2], "snare": [1, 3], "hat": [0.5, 1.5, 2.5, 3.5]},
    "snareheavy": {"kick": [0, 2.5], "snare": [1, 1.5, 3, 3.5], "hat": [0.5, 2]},
    "fourfloor": {"kick": [0, 1, 2, 3], "hat": [0.5, 1.5, 2.5, 3.5]},
}


def _env(n, k):
    return np.exp(-np.arange(n) / SR * k)


def _kick():
    n = int(0.20 * SR)
    return (np.sin(2 * np.pi * 65 * np.arange(n) / SR) * _env(n, 20)).astype("float32")


def _snare():
    n = int(0.16 * SR)
    tone = 0.3 * np.sin(2 * np.pi * 190 * np.arange(n) / SR)
    return (0.7 * (np.random.randn(n) + tone) * _env(n, 28)).astype("float32")


def _hat():
    n = int(0.05 * SR)
    x = np.diff(np.random.randn(n), prepend=0.0)
    return (0.5 * x * _env(n, 110)).astype("float32")


def _click():
    n = int(0.03 * SR)
    return (0.4 * np.sin(2 * np.pi * 1000 * np.arange(n) / SR) * _env(n, 150)).astype("float32")


SOUND = {"kick": _kick, "snare": _snare, "hat": _hat}


def gen(name, bpm, bars, preset):
    beat = 60.0 / bpm
    total_beats = (1 + bars) * 4
    buf = np.zeros(int((total_beats * beat + 0.5) * SR), dtype="float32")

    def place(snd, t):
        s = int(t * SR)
        buf[s : s + len(snd)] += snd

    for b in range(4):  # count-in bar
        place(_click(), b * beat)
    grid = []
    pat = PRESETS[preset]
    for bar in range(bars):
        base = (4 + bar * 4) * beat
        for lab, slots in pat.items():
            for s in slots:
                t = base + s * beat
                place(SOUND[lab](), t)
                grid.append((t, lab))
    grid.sort()
    sf.write(HERE / f"{name}.reference.wav", np.clip(buf, -1, 1), SR, subtype="PCM_16")
    json.dump({"bpm": bpm, "grid": grid}, open(HERE / f"{name}.grid.json", "w"))
    print(f"{name}: {len(buf)/SR:.1f}s, {len(grid)} notes {dict(Counter(l for _,l in grid))} @ {bpm} BPM ({preset})")


def rec(name):
    import sounddevice as sd

    ref, sr = sf.read(HERE / f"{name}.reference.wav")
    if ref.ndim == 1:
        ref = ref.reshape(-1, 1)
    out = sd.playrec(ref, samplerate=sr, channels=1)
    sd.wait()
    sf.write(HERE / f"{name}.mimic.wav", out, sr, subtype="PCM_16")
    print(f"OK recorded {len(out)/sr:.1f}s -> {name}.mimic.wav")


def _match(onsets, gt, delta, tol=0.08):
    used, pairs = set(), []
    for o in onsets:
        best, bd = -1, tol
        for j, tg in enumerate(gt):
            if j in used:
                continue
            d = abs(o - (tg + delta))
            if d < bd:
                bd, best = d, j
        if best >= 0:
            used.add(best)
            pairs.append((o, best))
    return pairs


def score(name, clip):
    g = json.load(open(HERE / f"{name}.grid.json"))
    grid = g["grid"]
    yf, _ = librosa.load(str(HERE / f"{name}.mimic.wav"), sr=T._SR, mono=True)
    feats = {}
    onsets = []
    for t in T._detect_onsets(yf, T._SR):
        f = T._features_at(yf, T._SR, t)
        if f["rms"] >= 0.005:
            onsets.append(t)
            feats[t] = f
    gt = np.array([t for t, _ in grid])
    gl = [l for _, l in grid]
    # Constrain to a plausible reaction+latency window. A free search can lock
    # onto an off-by-one-slot offset (equal match count, wrong labels) when the
    # grid is dense; the window is narrower than the slot spacing so only the
    # true alignment falls inside.
    best = max(np.arange(0.12, 0.30, 0.005), key=lambda d: len(_match(onsets, gt, d)))
    pairs = _match(onsets, gt, best)
    # sanity: if even the heuristic scores near-zero the labels are likely
    # misaligned, not the classifier — flag it rather than emit a bad clip.

    rows, labeled = [], []
    for o, j in pairs:
        true = gl[j]
        f = feats[o]
        labeled.append({"x": [f[k] for k in ["centroid", "sub100_ratio", "decay_s", "zcr", "flatness"]], "y": true})
        rows.append((true, PITCH2LAB[T._classify(f)], PITCH2LAB[T._classify_heuristic(f)]))
    n = len(rows)
    mc = sum(1 for t, m, h in rows if m == t)
    hc = sum(1 for t, m, h in rows if h == t)
    print(f"{name}: matched {n}/{len(grid)} (offset {best*1000:.0f}ms) | "
          f"model {mc}/{n}={mc/n:.2f}  heuristic {hc}/{n}={hc/n:.2f}")
    json.dump(labeled, open(HERE / f"{name}.labeled.json", "w"))

    # emit corpus clip (GT = intended grid at performed timing)
    dest = REPO / "tests" / "fixtures" / "clips" / clip
    shutil.copy(HERE / f"{name}.mimic.wav", str(dest) + ".wav")
    tpb, bpm = 480, g["bpm"]
    mid = mido.MidiFile(ticks_per_beat=tpb)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    ev = []
    for t, lab in grid:
        tick = int(round((t + best) * bpm / 60 * tpb))
        ev += [(tick, "on", LAB2PITCH[lab]), (tick + tpb // 8, "off", LAB2PITCH[lab])]
    ev.sort()
    last = 0
    for tick, k, p in ev:
        tr.append(mido.Message("note_on" if k == "on" else "note_off", note=p, velocity=90 if k == "on" else 0, time=tick - last, channel=9))
        last = tick
    mid.save(str(dest) + ".mid")
    json.dump({"tempo": bpm, "style": name, "notes": "mimic-a-beat auto-labeled"}, open(str(dest) + ".json", "w"), indent=2)
    print(f"  wrote corpus clip {clip}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "rec", "score"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--bpm", type=int, default=90)
    ap.add_argument("--bars", type=int, default=4)
    ap.add_argument("--preset", default="boombap", choices=list(PRESETS))
    ap.add_argument("--clip", default=None)
    a = ap.parse_args()
    if a.cmd == "gen":
        gen(a.name, a.bpm, a.bars, a.preset)
    elif a.cmd == "rec":
        rec(a.name)
    else:
        score(a.name, a.clip or a.name)


if __name__ == "__main__":
    main()
