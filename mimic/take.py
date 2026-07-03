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


def list_devices():
    import sounddevice as sd

    print(sd.query_devices())
    di, do = sd.default.device
    print(f"\ndefault input={di} output={do}")
    print("record through your real beatbox mic with:  rec --name X --input <N>")


def rec(name, input_dev=None, output_dev=None):
    import numpy as np
    import sounddevice as sd

    if input_dev is not None or output_dev is not None:
        cur_in, cur_out = sd.default.device
        sd.default.device = (
            input_dev if input_dev is not None else cur_in,
            output_dev if output_dev is not None else cur_out,
        )
    ref, sr = sf.read(HERE / f"{name}.reference.wav")
    if ref.ndim == 1:
        ref = ref.reshape(-1, 1)
    out = sd.playrec(ref, samplerate=sr, channels=1)
    sd.wait()
    out = np.asarray(out).reshape(-1)
    sf.write(HERE / f"{name}.mimic.wav", out, sr, subtype="PCM_16")

    # Level check — the classifier learns the timbre AT THIS LEVEL, so record at
    # the same gain/distance you'll actually beatbox at, and keep it healthy.
    peak = float(np.max(np.abs(out)))
    rms = float(np.sqrt(np.mean(out**2)))
    warn = ""
    if peak < 0.08:
        warn = "   ** too quiet — raise mic gain or get closer (your Live takes were this quiet)"
    elif peak > 0.99:
        warn = "   ** clipping — lower the gain"
    print(f"OK recorded {len(out)/sr:.1f}s -> {name}.mimic.wav | peak {peak:.2f} rms {rms:.3f}{warn}")


def _labels_look_sane(matched: int, total: int, heuristic_correct: int) -> tuple[bool, str]:
    """Gate on auto-label quality before a take becomes a corpus fixture.

    If even the hand-tuned heuristic scores near-zero on the aligned labels,
    the alignment (not the classifier) is almost certainly wrong — emitting
    the clip would bake mislabels into the regression corpus. Same if most of
    the grid never matched an onset (the performance didn't follow the
    reference).
    """
    if total > 0 and matched / total < 0.5:
        return False, (
            f"only {matched}/{total} grid notes matched an onset — the take "
            "doesn't follow the reference; not emitting a corpus clip"
        )
    if matched > 0 and heuristic_correct / matched < 0.35:
        return False, (
            f"heuristic agrees with only {heuristic_correct}/{matched} labels — "
            "alignment looks wrong (off-by-a-slot?); not emitting a corpus clip"
        )
    return True, ""


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


def score(name, clip, force=False):
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

    rows, labeled = [], []
    for o, j in pairs:
        true = gl[j]
        f = feats[o]
        labeled.append({"y": true})  # features are re-extracted from grids at train time
        rows.append((true, PITCH2LAB[T._classify(yf, T._SR, o, f)], PITCH2LAB[T._classify_heuristic(f)]))
    n = len(rows)
    mc = sum(1 for t, m, h in rows if m == t)
    hc = sum(1 for t, m, h in rows if h == t)
    if n == 0:
        # Zero matches is the most extreme misalignment — report it instead
        # of crashing on the accuracy division below.
        print(f"{name}: matched 0/{len(grid)} — no onset landed near any grid note")
        ok, why = _labels_look_sane(0, len(grid), 0)
    else:
        print(f"{name}: matched {n}/{len(grid)} (offset {best*1000:.0f}ms) | "
              f"model {mc}/{n}={mc/n:.2f}  heuristic {hc}/{n}={hc/n:.2f}")

    # Sanity-gate before anything is persisted: a misaligned take must not
    # bake mislabels into the training data or the regression corpus. (Note:
    # this gate is calibrated for playrec takes; a take captured some other
    # way can sit outside the 0.12-0.30s offset search and fail here even
    # when a wider alignment would fit — that's what --force is for.)
    if n > 0:
        ok, why = _labels_look_sane(n, len(grid), hc)
    if not ok and not force:
        print(f"  ** {why} (pass --force to override)")
        return
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
    ap.add_argument("cmd", choices=["gen", "rec", "score", "devices"])
    ap.add_argument("--name", default=None)
    ap.add_argument("--bpm", type=int, default=90)
    ap.add_argument("--bars", type=int, default=4)
    ap.add_argument("--preset", default="boombap", choices=list(PRESETS))
    ap.add_argument("--clip", default=None)
    ap.add_argument("--input", type=int, default=None, help="input device index (see `devices`)")
    ap.add_argument("--output", type=int, default=None, help="output device index (headphones)")
    ap.add_argument("--force", action="store_true", help="emit labels/clip even if the sanity gate fails")
    a = ap.parse_args()
    if a.cmd == "devices":
        list_devices()
        return
    if not a.name:
        ap.error("--name is required")
    if a.cmd == "gen":
        gen(a.name, a.bpm, a.bars, a.preset)
    elif a.cmd == "rec":
        rec(a.name, a.input, a.output)
    else:
        score(a.name, a.clip or a.name, force=a.force)


if __name__ == "__main__":
    main()
