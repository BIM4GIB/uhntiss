"""Guided recording sessions: turn 20 minutes at the mic into dataset rows.

Walks a PLAN of takes one by one: synthesizes the reference, lets you listen,
records the imitation sample-synced, scores it against the grid, and ingests
accepted takes into ``datasets/`` — retake/skip/quit at every step, progress
saved so a session can stop and resume anytime.

  uv run python -m mimic.session --plan starter_bass [--input N --output M]
  uv run python -m mimic.session --list

Drum takes use the existing ``mimic/take.py`` harness (its presets + scoring);
tonal takes use ``mimic/tonal.py``. ``role=eval`` takes are recorded LAST and
must never be used for tuning — they are the honest number.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_PROGRESS = HERE / ".session_progress.json"

# --- plans ----------------------------------------------------------------------
# (name, voice, riff/preset, key, bpm, role). Keys rotate so calibration
# doesn't overfit one pitch class; eval rows use keys/tempos never seen in
# calibrate rows. ~25s per take incl. listen -> a plan of 20 is ~15 min.

_BASS_CAL = [
    ("b01", "bass", "roots", "F", 90), ("b02", "bass", "roots", "A", 120),
    ("b03", "bass", "pump8", "F", 90), ("b04", "bass", "pump8", "A", 120),
    ("b05", "bass", "funk",  "F", 90), ("b06", "bass", "funk",  "A", 120),
    ("b07", "bass", "roots", "G", 105), ("b08", "bass", "pump8", "G", 105),
    ("b09", "bass", "funk",  "G", 105), ("b10", "bass", "pump8", "E", 120),
    ("b11", "bass", "roots", "E", 140), ("b12", "bass", "funk",  "A", 140),
]
_BASS_EVAL = [
    ("be1", "bass", "roots", "Bb", 100), ("be2", "bass", "pump8", "Bb", 100),
    ("be3", "bass", "funk",  "Bb", 100), ("be4", "bass", "pump8", "D", 130),
    ("be5", "bass", "funk",  "D", 130), ("be6", "bass", "roots", "D", 85),
    ("be7", "bass", "pump8", "F#", 85), ("be8", "bass", "funk",  "F#", 115),
]

_LEAD_CAL = [
    ("l01", "lead", "steps", "C", 100), ("l02", "lead", "steps", "F", 120),
    ("l03", "lead", "arp",   "C", 100), ("l04", "lead", "arp",   "F", 120),
    ("l05", "lead", "held",  "C", 100), ("l06", "lead", "held",  "F", 120),
]
_LEAD_EVAL = [
    ("le1", "lead", "steps", "G", 110), ("le2", "lead", "arp", "G", 110),
    ("le3", "lead", "held", "D", 90), ("le4", "lead", "arp", "D", 90),
]

_DRONE = [
    ("d01", "drone", "hold", "C", 100), ("d02", "drone", "hold", "F#", 100),
    ("d03", "drone", "chord", "C", 100), ("d04", "drone", "chord", "A", 100),
    ("d05", "drone", "hold", "Eb", 100),
]

PLANS: dict[str, list[tuple]] = {
    # (name, voice, riff, key, bpm, role)
    "starter_bass": [(*r, "calibrate") for r in _BASS_CAL] + [(*r, "eval") for r in _BASS_EVAL],
    "starter_lead": [(*r, "calibrate") for r in _LEAD_CAL] + [(*r, "eval") for r in _LEAD_EVAL],
    "starter_drone": [(*r, "calibrate") for r in _DRONE],
}


def _load_progress() -> dict:
    try:
        return json.loads(_PROGRESS.read_text())
    except (OSError, ValueError):
        return {}


def _save_progress(p: dict) -> None:
    _PROGRESS.write_text(json.dumps(p, indent=2))


def run_plan(plan_name: str, input_dev: int | None, output_dev: int | None) -> None:
    from mimic import tonal

    rows = PLANS[plan_name]
    progress = _load_progress()
    done = set(progress.get(plan_name, []))
    todo = [r for r in rows if r[0] not in done]
    print(f"plan {plan_name}: {len(rows) - len(todo)}/{len(rows)} done, {len(todo)} to go\n")

    for name, voice, riff, key, bpm, role in todo:
        tonal.gen(name, voice, riff, key, bpm)
        while True:
            cmd = input(
                f"[{name}] {voice}/{riff} {key}@{bpm:g} ({role}) — "
                "ENTER=record (you'll hear it as you hum), l=listen first, s=skip, q=quit: "
            ).strip().lower()
            if cmd == "q":
                print("session paused — progress saved; rerun to resume")
                return
            if cmd == "s":
                break
            if cmd == "l":
                _play_reference(name, output_dev)
                continue
            tonal.rec(name, input_dev, output_dev)
            if tonal.score(name, role=role):
                done.add(name)
                progress[plan_name] = sorted(done)
                _save_progress(progress)
                break
            retry = input("  retake? (ENTER=yes, k=keep anyway, s=skip): ").strip().lower()
            if retry == "s":
                break
            if retry == "k":
                tonal.score(name, role=role, force=True)
                done.add(name)
                progress[plan_name] = sorted(done)
                _save_progress(progress)
                break
    remaining = [r for r in rows if r[0] not in done]
    if not remaining:
        print(f"\nplan {plan_name} COMPLETE — {len(rows)} takes in datasets/")
    else:
        print(f"\n{len(remaining)} takes remaining in {plan_name}")


def _play_reference(name: str, output_dev: int | None) -> None:
    import sounddevice as sd
    import soundfile as sf

    y, sr = sf.read(str(HERE / f"{name}.reference.wav"))
    sd.play(y, sr, device=output_dev)
    sd.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=None, choices=list(PLANS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--input", type=int, default=None, help="input device index")
    ap.add_argument("--output", type=int, default=None, help="output device index (headphones!)")
    a = ap.parse_args()
    if a.list or not a.plan:
        progress = _load_progress()
        for pname, rows in PLANS.items():
            done = len(set(progress.get(pname, [])))
            print(f"  {pname:<16} {done}/{len(rows)} recorded")
        if not a.plan:
            print("\nrun one with: uv run python -m mimic.session --plan <name>")
        return
    run_plan(a.plan, a.input, a.output)


if __name__ == "__main__":
    main()
