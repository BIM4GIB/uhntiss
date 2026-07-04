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


# --- one-shot verbs (drive these from the M4L panels — no terminal needed) ------


def _next_row(plan_name: str, progress: dict):
    done = set(progress.get(plan_name, [])) | set(progress.get(plan_name + "_skipped", []))
    for row in PLANS[plan_name]:
        if row[0] not in done:
            return row
    return None


def _progress_str(plan_name: str, progress: dict) -> str:
    done = len(set(progress.get(plan_name, [])))
    return f"{done}/{len(PLANS[plan_name])}"


def cmd_next(plan_name: str) -> None:
    progress = _load_progress()
    row = _next_row(plan_name, progress)
    if row is None:
        print(f"{plan_name} COMPLETE ({_progress_str(plan_name, progress)}) — thank you!")
        return
    name, voice, riff, key, bpm, role = row
    print(f"next: {name} · {voice}/{riff} in {key} @ {bpm:g} ({role}) · {_progress_str(plan_name, progress)} done")


def cmd_record(plan_name: str, input_dev: int | None, output_dev: int | None) -> int:
    """Gen + play/record + score the next take. Advances only on PASS."""
    from mimic import tonal

    progress = _load_progress()
    row = _next_row(plan_name, progress)
    if row is None:
        print(f"{plan_name} COMPLETE — nothing to record")
        return 0
    name, voice, riff, key, bpm, role = row
    tonal.gen(name, voice, riff, key, bpm)
    print(f"REC · imitate what you hear · {name} {voice}/{riff} {key}@{bpm:g}")
    tonal.rec(name, input_dev, output_dev)
    progress["_last"] = {"plan": plan_name, "name": name, "role": role}
    _save_progress(progress)
    if tonal.score(name, role=role):
        progress = _load_progress()
        progress.setdefault(plan_name, []).append(name)
        _save_progress(progress)
        nxt = _next_row(plan_name, _load_progress())
        print(f"PASS · {_progress_str(plan_name, _load_progress())} done"
              + (f" · next: {nxt[0]} {nxt[1]}/{nxt[2]} {nxt[3]}@{nxt[4]:g}" if nxt else " · PLAN COMPLETE"))
        return 0
    print("RETRY · hit 'rec sample' again (or 'keep' to accept it anyway)")
    return 0


def cmd_keep(plan_name: str) -> None:
    """Force-ingest the last attempted take (the gate said no; you say yes)."""
    from mimic import tonal

    progress = _load_progress()
    last = progress.get("_last")
    if not last or last.get("plan") != plan_name:
        print("nothing to keep — record a take first")
        return
    tonal.score(last["name"], role=last.get("role", "calibrate"), force=True)
    progress.setdefault(plan_name, []).append(last["name"])
    _save_progress(progress)
    print(f"KEPT · {_progress_str(plan_name, progress)} done")


def cmd_skip(plan_name: str) -> None:
    progress = _load_progress()
    row = _next_row(plan_name, progress)
    if row is None:
        print(f"{plan_name} COMPLETE — nothing to skip")
        return
    progress.setdefault(plan_name + "_skipped", []).append(row[0])
    _save_progress(progress)
    print(f"skipped {row[0]} · {_progress_str(plan_name, progress)} done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "next", "record", "keep", "skip"])
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
    if a.cmd == "run":
        run_plan(a.plan, a.input, a.output)
    elif a.cmd == "next":
        cmd_next(a.plan)
    elif a.cmd == "record":
        cmd_record(a.plan, a.input, a.output)
    elif a.cmd == "keep":
        cmd_keep(a.plan)
    elif a.cmd == "skip":
        cmd_skip(a.plan)


if __name__ == "__main__":
    main()
