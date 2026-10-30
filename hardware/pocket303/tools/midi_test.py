#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mido>=1.3", "python-rtmidi>=1.5"]
# ///
"""Bench test for the Pocket 303 placeholder firmware over MIDI.

    uv run hardware/pocket303/tools/midi_test.py            # play the riff on the first "Daisy" port
    uv run hardware/pocket303/tools/midi_test.py --list     # show MIDI output ports
    uv run hardware/pocket303/tools/midi_test.py --port "IAC"   # any port substring
    uv run hardware/pocket303/tools/midi_test.py --sweep    # riff + a cutoff sweep on CC 74
    uv run hardware/pocket303/tools/midi_test.py --reset    # MIDI System Reset: Seed reboots into DFU

The riff has accents (velocity 120) and slides (overlapping notes), so you hear all
three 303 behaviours the placeholder implements.
"""
from __future__ import annotations

import argparse
import sys
import time

import mido

BPM = 130.0
STEP = 60.0 / BPM / 4.0  # 16th note
ROOT = 36  # C2

# (semitone offset, accent, slide-into-next) — a two-bar acid line
RIFF = [
    (0, True, False), (0, False, False), (12, True, True), (0, False, False),
    (3, False, False), (0, False, True), (7, True, False), (0, False, False),
    (0, True, False), (10, False, True), (12, False, False), (0, False, False),
    (0, False, False), (5, True, True), (3, False, False), (0, False, False),
]


def pick_port(sub: str) -> str:
    names = mido.get_output_names()
    for n in names:
        if sub.lower() in n.lower():
            return n
    sys.exit(f"no MIDI output port containing {sub!r}; have: {names}")


def play(out: mido.ports.BaseOutput, loops: int, sweep: bool) -> None:
    out.send(mido.Message("control_change", control=74, value=58))   # cutoff
    out.send(mido.Message("control_change", control=71, value=85))   # resonance
    out.send(mido.Message("control_change", control=12, value=70))   # env mod
    out.send(mido.Message("control_change", control=75, value=45))   # decay
    out.send(mido.Message("control_change", control=76, value=90))   # accent
    out.send(mido.Message("control_change", control=5, value=40))    # slide time
    out.send(mido.Message("control_change", control=7, value=110))   # volume
    total = loops * len(RIFF)
    for i in range(total):
        semi, accent, slide = RIFF[i % len(RIFF)]
        note = ROOT + semi
        vel = 120 if accent else 80
        if sweep:
            cut = int(20 + 100 * (0.5 - 0.5 * __import__("math").cos(2 * 3.14159 * i / total)))
            out.send(mido.Message("control_change", control=74, value=cut))
        out.send(mido.Message("note_on", note=note, velocity=vel))
        if slide:
            nxt = ROOT + RIFF[(i + 1) % len(RIFF)][0]
            time.sleep(STEP * 0.9)
            out.send(mido.Message("note_on", note=nxt, velocity=vel))  # overlap -> slide
            time.sleep(STEP * 0.1)
            out.send(mido.Message("note_off", note=note))
            time.sleep(STEP * 0.55)
            out.send(mido.Message("note_off", note=nxt))
            time.sleep(STEP * 0.45)
            continue
        time.sleep(STEP * 0.55)
        out.send(mido.Message("note_off", note=note))
        time.sleep(STEP * 0.45)
    out.send(mido.Message("control_change", control=123, value=0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="Daisy", help="substring of the MIDI output port name (default: Daisy)")
    ap.add_argument("--list", action="store_true", help="list MIDI output ports and exit")
    ap.add_argument("--loops", type=int, default=2, help="times to play the two-bar riff")
    ap.add_argument("--sweep", action="store_true", help="sweep cutoff (CC 74) while playing")
    ap.add_argument("--reset", action="store_true", help="send System Reset (0xFF): the Seed reboots into DFU mode")
    args = ap.parse_args()

    if args.list:
        for n in mido.get_output_names():
            print(n)
        return

    name = pick_port(args.port)
    print(f"-> {name}")
    with mido.open_output(name) as out:
        if args.reset:
            out.send(mido.Message("reset"))
            print("sent System Reset; the Seed should now be in DFU mode (dfu-util -l shows 0483:df11)")
            return
        play(out, args.loops, args.sweep)
    print("done")


if __name__ == "__main__":
    main()
