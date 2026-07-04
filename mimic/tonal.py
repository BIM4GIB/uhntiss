"""Reference-linked tonal recording — ground truth for bass / lead / drone.

The tonal twin of ``mimic/take.py``: synthesize a reference riff (count-in +
riff, rendered with an instrument-like tone), play it to headphones while
recording the vocal imitation **sample-synced** (``sounddevice.playrec``), so
the reference grid IS the ground truth for what the performer *meant*. Score
the take with the voice's own transcriber against that grid, sanity-gate it,
and ingest the (reference, take, labels) trio into ``datasets/`` with a
manifest line.

Every accepted pair serves three purposes:
- **calibrate**  — fit the voice's thresholds to THIS performer's voice
- **eval**       — held-out honest accuracy (never tuned against)
- **train**      — future per-user models (embedding/prototype classifiers)

Usage (usually driven by ``python -m mimic.session``):
  uv run python -m mimic.tonal gen   --name b01 --voice bass --riff pump8 --key F --bpm 100
  uv run python -m mimic.tonal rec   --name b01 [--input N --output M]
  uv run python -m mimic.tonal score --name b01 --role calibrate
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATASETS = REPO / "datasets"

# Ingest gates: a take must be a plausible imitation before it becomes data.
# (note F1 vs the reference grid; deliberately loose — the point of the data
# is to MEASURE the transcriber, so we only reject takes where the performer
# clearly lost the riff or the alignment failed.)
_MIN_MATCH_FRACTION = 0.5   # at least half the reference notes found any match
_MIN_RECALL = 0.3           # ...and some of them pitch-correct

# --- riff library -------------------------------------------------------------
# Each riff: list of (start_beat, dur_beats, degree) over a 2-bar loop,
# played twice. Degrees are semitone offsets from the take's root note.
# Archetypes chosen to stress ONE failure mode each.

BASS_RIFFS = {
    # sustained roots+fifths: pitch/octave stability, held-note durations
    "roots": [(0, 2, 0), (2, 2, 7), (4, 2, 0), (6, 1, 5), (7, 1, 7)],
    # pumping same-pitch 8ths: re-articulation, staccato, velocity accents
    "pump8": [(b * 0.5, 0.4, 0) for b in range(12)] + [(6, 0.4, 3), (6.5, 0.4, 3), (7, 0.9, 5)],
    # syncopated with rests + octave jump: timing, register jumps
    "funk": [(0, 0.4, 0), (0.75, 0.4, 0), (1.5, 0.4, 10), (2.5, 0.4, 12),
             (3.25, 0.4, 7), (4, 0.4, 0), (5.5, 0.4, 3), (6.25, 0.4, 5), (7, 0.9, 0)],
}

LEAD_RIFFS = {
    # stepwise melody: basic pitch accuracy in the lead register
    "steps": [(0, 1, 0), (1, 1, 2), (2, 1, 4), (3, 1, 5), (4, 1, 7), (5, 1, 5), (6, 2, 4)],
    # arpeggio leaps: octave continuity across jumps
    "arp": [(0, 0.9, 0), (1, 0.9, 7), (2, 0.9, 12), (3, 0.9, 7),
            (4, 0.9, 0), (5, 0.9, 4), (6, 1.9, 7)],
    # held notes with room for vibrato: segmentation must not shatter them
    "held": [(0, 3, 0), (4, 3, 4)],
}

DRONE_RIFFS = {
    "hold": [(0, 8, 0)],                       # one held root
    "chord": [(0, 8, 0), (0, 8, 7), (0, 8, 12)],  # stacked fifth+octave (sequential hum)
}

RIFFS = {"bass": BASS_RIFFS, "lead": LEAD_RIFFS, "drone": DRONE_RIFFS}

# Root register per voice (key letter -> MIDI in the voice's natural octave).
_KEY_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
           "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
_ROOT_OCTAVE_BASE = {"bass": 28, "lead": 55, "drone": 48}  # E1 / G3 / C3 neighborhoods


def _root_midi(voice: str, key: str) -> int:
    base = _ROOT_OCTAVE_BASE[voice]
    pc = _KEY_PC[key[0].upper() + key[1:]]
    midi = (base // 12) * 12 + pc
    if midi < base:
        midi += 12
    return midi


# --- reference synthesis --------------------------------------------------------


def _env(n: int, attack_s: float, decay_k: float) -> np.ndarray:
    e = np.exp(-np.arange(n) / SR * decay_k)
    a = max(1, int(attack_s * SR))
    e[:a] *= np.linspace(0, 1, a)
    return e


def _tone(voice: str, midi: int, dur_s: float) -> np.ndarray:
    """Instrument-ish reference tone: enough timbre to imitate the register
    and articulation. (Upgrade path: render the reference MIDI through an
    actual Live instrument via the bridge — same grid, real sound.)"""
    f = 440.0 * 2 ** ((midi - 69) / 12)
    n = int(dur_s * SR)
    t = np.arange(n) / SR
    if voice == "bass":
        y = 0.6 * np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
        y *= _env(n, 0.005, 6.0)
    elif voice == "lead":
        y = 0.4 * np.sin(2 * np.pi * f * t) + 0.2 * np.sign(np.sin(2 * np.pi * f * t))
        y *= _env(n, 0.01, 3.0)
    else:  # drone: slow attack, no decay
        y = 0.35 * np.sin(2 * np.pi * f * t) + 0.15 * np.sin(2 * np.pi * 2 * f * t)
        y *= np.minimum(1.0, t / 0.5)
    return y.astype(np.float32)


def _click() -> np.ndarray:
    n = int(0.03 * SR)
    return (0.5 * np.sin(2 * np.pi * 1000 * np.arange(n) / SR) * _env(n, 0.001, 150)).astype(
        np.float32
    )


def _paths(name: str) -> dict[str, Path]:
    return {
        "reference": HERE / f"{name}.reference.wav",
        "grid": HERE / f"{name}.notegrid.json",
        "take": HERE / f"{name}.hum.wav",
    }


def gen(name: str, voice: str, riff: str, key: str, bpm: float, loops: int = 2) -> None:
    """Reference audio (1 bar count-in + riff x loops) + notegrid labels."""
    riff_notes = RIFFS[voice][riff]
    root = _root_midi(voice, key)
    beat = 60.0 / bpm
    loop_beats = 8.0  # riffs are written over 2 bars of 4/4
    total_s = (4 + loops * loop_beats) * beat + 0.5

    buf = np.zeros(int(total_s * SR), dtype=np.float32)
    for b in range(4):  # count-in bar
        s = int(b * beat * SR)
        c = _click()
        buf[s : s + len(c)] += c

    grid: list[list] = []  # [t_s, midi, dur_s]
    for loop in range(loops):
        base = (4 + loop * loop_beats) * beat
        for start_b, dur_b, degree in riff_notes:
            t0 = base + start_b * beat
            dur_s = max(0.1, dur_b * beat)
            tone = _tone(voice, root + degree, dur_s)
            s = int(t0 * SR)
            buf[s : s + len(tone)] += tone[: len(buf) - s]
            grid.append([round(t0, 4), root + degree, round(dur_s, 3)])
    grid.sort()

    p = _paths(name)
    sf.write(p["reference"], np.clip(buf, -1, 1), SR, subtype="PCM_16")
    json.dump(
        {"voice": voice, "riff": riff, "key": key, "bpm": bpm, "loops": loops,
         "notes": [[t, m] for t, m, _ in grid], "durs": [d for _, _, d in grid]},
        open(p["grid"], "w"),
    )
    n_notes = len(grid)
    print(f"{name}: {voice}/{riff} in {key} @ {bpm:g} BPM — {n_notes} notes, "
          f"{total_s:.1f}s (listen once, then imitate in time)")


def rec(name: str, input_dev: int | None = None, output_dev: int | None = None) -> None:
    """Sample-synced playrec: hear the reference, hum along; the grid is GT.
    (Same proven path as mimic/take.py's drum rec, different filenames.)"""
    import sounddevice as sd

    if input_dev is not None or output_dev is not None:
        cur_in, cur_out = sd.default.device
        sd.default.device = (
            input_dev if input_dev is not None else cur_in,
            output_dev if output_dev is not None else cur_out,
        )
    p = _paths(name)
    ref, sr = sf.read(p["reference"])
    if ref.ndim == 1:
        ref = ref.reshape(-1, 1)
    out = sd.playrec(ref, samplerate=sr, channels=1)
    sd.wait()
    out = np.asarray(out).reshape(-1)
    sf.write(p["take"], out, sr, subtype="PCM_16")
    peak = float(np.max(np.abs(out)))
    warn = ""
    if peak < 0.08:
        warn = "   ** too quiet — raise mic gain or get closer"
    elif peak > 0.99:
        warn = "   ** clipping — lower the gain"
    print(f"OK recorded {len(out)/sr:.1f}s -> {p['take'].name} | peak {peak:.2f}{warn}")


def _transcriber(voice: str):
    from mouthflow.devices import get_device_by_id

    return get_device_by_id(voice).transcriber


def _pipeline_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True
        ).strip()
    except Exception:
        return "unknown"


def score(
    name: str,
    role: str = "calibrate",
    force: bool = False,
    offset_range: tuple[float, float] | None = None,
) -> bool:
    """Score the take against its grid, gate, and ingest into datasets/."""
    from eval.note_eval import OFFSET_MAX, OFFSET_MIN
    from eval.note_eval import score as note_score

    p = _paths(name)
    grid = json.load(open(p["grid"]))
    voice = grid["voice"]
    stats = note_score(
        _transcriber(voice), p["take"], grid,
        offset_range=offset_range or (OFFSET_MIN, OFFSET_MAX),
    )
    n_ref = stats["notes_ref"]
    matched = stats["tp"] + stats["octave_err"] + stats["wrong_pitch"]
    print(f"{name}: matched {matched}/{n_ref}  P {stats['precision']:.2f} "
          f"R {stats['recall']:.2f} F1 {stats['f1']:.2f}  octave_err {stats['octave_err']}  "
          f"offset {stats['offset']*1000:.0f}ms")

    ok = n_ref > 0 and matched / n_ref >= _MIN_MATCH_FRACTION and stats["recall"] >= _MIN_RECALL
    if not ok and not force:
        print("  ** take doesn't track the reference well enough to be data "
              "(lost the riff, or bad alignment) — retake, or --force to keep")
        return False

    dest = DATASETS / voice
    dest.mkdir(parents=True, exist_ok=True)
    import shutil

    files = {}
    for kind in ("reference", "grid", "take"):
        if not p[kind].exists():
            continue  # live-flow rows have no synthesized reference file
        target = dest / p[kind].name
        shutil.copy2(p[kind], target)
        try:
            files[kind] = str(target.relative_to(REPO))
        except ValueError:  # datasets dir outside the repo (tests, custom home)
            files[kind] = str(target)
    entry = {
        "take": name, "voice": voice, "riff": grid["riff"], "key": grid["key"],
        "bpm": grid["bpm"], "role": role, "date": time.strftime("%Y-%m-%d %H:%M"),
        "pipeline_sha": _pipeline_sha(), "scores": stats, "files": files,
    }
    DATASETS.mkdir(exist_ok=True)
    with open(DATASETS / "manifest.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  ingested -> datasets/{voice}/ (role={role})")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "rec", "score"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--voice", default="bass", choices=list(RIFFS))
    ap.add_argument("--riff", default="roots")
    ap.add_argument("--key", default="F")
    ap.add_argument("--bpm", type=float, default=100.0)
    ap.add_argument("--role", default="calibrate", choices=["calibrate", "eval", "train"])
    ap.add_argument("--input", type=int, default=None)
    ap.add_argument("--output", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "gen":
        gen(a.name, a.voice, a.riff, a.key, a.bpm)
    elif a.cmd == "rec":
        rec(a.name, a.input, a.output)
    else:
        score(a.name, role=a.role, force=a.force)


if __name__ == "__main__":
    main()
