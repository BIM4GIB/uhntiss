"""Show what the drum quantizer actually does to a clip's timing/feel.

For "the drums feel off" complaints: prints the detected tempo + confidence,
whether the grid was trusted (hard-snap) or the onsets were emitted raw, how
far each hit was moved by quantization, and a swing estimate — so we can see if
the feel is being killed by over-snapping, left sloppy by an untrusted tempo,
or thrown by a wrong grid.

Run: ``uv run python -m eval.timing_probe path/to/clip.wav [--tempo N]``
"""

from __future__ import annotations

import argparse
from pathlib import Path

import librosa
import numpy as np

from mouthflow import signal
from mouthflow.devices.drum import tempo as drum_tempo
from mouthflow.devices.drum.classify import DROP, _classify

_GM = {36: "kick", 38: "snare", 42: "hat", 46: "hatO", 39: "perc"}


def probe(wav: Path, forced_tempo: float | None = None) -> None:
    y, sr = librosa.load(str(wav), sr=signal._SR, mono=True)

    kept = []  # (t_raw, note)
    for t in signal.detect_onsets(y, sr):
        f = signal.features_at(y, sr, t)
        note = _classify(y, sr, t, f)
        if note != DROP:
            kept.append((float(t), note))
    onsets = np.array([t for t, _ in kept])

    if forced_tempo:
        bpm, conf = float(forced_tempo), 1.0
    else:
        bpm, conf = drum_tempo._detect_tempo(y, sr, onsets)
    trust = conf >= drum_tempo._QUANT_CONF_MIN

    print(f"\n{wav.name}: {len(kept)} hits | tempo {bpm:.1f} BPM | confidence {conf:.2f} "
          f"-> {'QUANTISED (16th snap)' if trust else 'RAW (tempo not trusted)'}")

    if not trust:
        print("  tempo untrusted -> onsets emitted raw (no grid). If this feels sloppy,")
        print("  the fix is better tempo confidence, not quantization. Try --tempo N.")
        return

    if forced_tempo is None:
        bpm = drum_tempo._refine_tempo(onsets, bpm)
    phase = drum_tempo._grid_phase(onsets, bpm)
    step = 60.0 / bpm / 4.0  # 16th-note seconds

    print(f"  refined {bpm:.2f} BPM | grid phase {phase:+.3f} of a 16th ({phase*step*1000:+.0f} ms)")
    print(f"  {'hit':>6}  {'raw(s)':>8}  {'moved(ms)':>9}  {'16th#':>5}  slot")
    disp = []
    swing = {0: [], 1: []}  # residual ms on even vs odd 16th slots (pre-snap)
    for t, note in kept:
        q = drum_tempo._quantise_grid(t, bpm, phase)
        moved = (q - t) * 1000.0
        disp.append(abs(moved))
        idx = int(round(t / step - phase))
        resid = (t - (idx + phase) * step) * 1000.0
        swing[idx % 2].append(resid)
        print(f"  {_GM.get(note, note):>6}  {t:8.3f}  {moved:>+9.0f}  {idx:>5}  {'on ' if idx%2==0 else 'off'}")

    print(f"\n  quantization displacement: mean {np.mean(disp):.0f} ms, max {np.max(disp):.0f} ms")
    if np.mean(disp) > 35:
        print("  -> hits are being moved a lot; hard 16th-snap may be flattening the groove.")
    on = np.mean(swing[0]) if swing[0] else 0.0
    off = np.mean(swing[1]) if swing[1] else 0.0
    print(f"  swing lean: on-beats {on:+.0f} ms, off-beats {off:+.0f} ms (pre-snap residual)")
    if off - on > 15:
        print("  -> off-beats lag the grid (swung feel) that a straight 16th-snap discards.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", type=Path)
    ap.add_argument("--tempo", type=float, default=None)
    a = ap.parse_args()
    probe(a.wav, a.tempo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
