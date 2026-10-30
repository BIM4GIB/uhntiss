# Pocket 303 — your own 303 as a Max for Live gen~ instrument

A mono acid voice written as **one gen~ codebox** (`pocket303.genexpr`), wrapped in a Max for
Live instrument (`build/Pocket 303.amxd`). The same gen~ patch is meant to run unchanged on the
Daisy Seed3 box in [`hardware/pocket303/`](../../hardware/pocket303/README.md) through Oopsy, so
every parameter is 0..1 and named after the board's pots.

```
MIDI ─► midiin ─► midiparse ─► js pocket303_notes.js ─► unpack ─► gen~ (pocket303.genexpr) ─► plugout~
                               mono note logic:          note · held · accent · slide · trig
                               last-note priority,
                               vel ≥ 100 = accent,
                               overlap = slide (no retrigger, glide)
live.dial × 8 (0..100 %) ─► / 100. ─► prepend <param> ─► gen~        Wave menu ─► prepend square
Sound menu (umenu) ─► Acid Trojan / Insurgency lasers / Insurgency sweep ─► writes the dials
```

| File | What |
|------|------|
| `pocket303.genexpr` | The voice. polyBLEP saw/square → 4-pole ladder (2× oversampled, soft-saturated input, half-sample feedback) → VCA → soft clip. 303 envelopes: decay-only MEG (30 ms…2.4 s, accent forces ≤ 200 ms), accent sweep through a 12 ms RC (the "wow", stacks on repeated accents), VCA with 3 ms attack / 3 s hold-decay / 10 ms release, RC slide on the note number. |
| `pocket303_notes.js` | Max `js`: the mono note stack. Outputs `note held accent slide trig`; `trig` is a counter so a note-off + note-on inside one signal vector still retriggers. |
| `build_device.py` | Writes `build/Pocket 303.amxd` (+ `.maxpat`, `pocket303.gendsp`). `--install` copies device + js to `~/Music/Ableton/User Library/Devices/Pocket 303/` (Live loads the js from next to the .amxd). |
| `proto/` | The **blind-tuning harness**: `voice.h` is a C mirror of the codebox (same names, same math, gen~ History discipline), `host.c` renders the three test clips through it, `analyze_grid.py` measures per-note spectral-peak trajectories. Change the DSP here first, measure, then port the lines to the genexpr. |

## Parameters (gen~ names, all 0..1)

`cutoff` 150 Hz…4.8 kHz (exponential) · `resonance` (ladder feedback 0…4, self-oscillates at the top) ·
`envmod` up to +2.4 octaves of MEG · `decay` MEG time · `accent` depth (filter +0.5 oct, VCA +70 %) ·
`tuning` ±12 st (0.5 = 0) · `glide` 12…300 ms · `volume` · `square` 0/1 (saw/square).
`glide`/`square` rather than slide/wave because those are gen operator names; the Daisy board JSON
aliases map them to the pots.

Inputs: `in 1` note (MIDI), `in 2` held, `in 3` accent, `in 4` slide, `in 5` trig counter.
Outputs: `out 1/2` audio, `out 3` gate (LED on the hardware).

## Tuning targets (from the records, via the Phoscyon renders René accepted)

| Sound | Dials (cut reso env dec acc tune slide vol) | What the prototype measures |
|-------|---------------------------------------------|-----------------------------|
| Acid Trojan riff | 30 79 67 20 79 0 30 80 | resonant peak 1.16 kHz → 580 Hz within 100 ms on every retriggered 16th (Phoscyon: 1.3 kHz → 430 Hz) |
| Insurgency lasers | 30 93 71 25 87 0 30 80 | accented notes step 3rd → 2nd → 1st harmonic (1314 → 861 → 452 Hz); slid notes hold |
| Insurgency sweep | 16 90 20 79 55 0 30 80 + Cutoff automation 16 % → 55 % over 112 beats | peak climbs 3rd → ~13th harmonic of A#2 (345 → ~1500 Hz) |

Reproduce: `cc -O2 -o proto/host proto/host.c -lm && python3 proto/render.py p && .venv/bin/python proto/analyze_grid.py proto/renders_p/lasers.wav 136 0.25 16`.

## Status (2026-10-30)

- Device builds, Live lists it under Max for Live ▸ Max Instrument and loads it (`MxDeviceInstrument`).
- **Compile fixed in v0.6**: Live's log (`~/Library/Preferences/Ableton/Live 12.x/Log.txt`, lines
  `error: Message from Max: ...`) showed `codebox: function definitions not allowed here` for every load
  up to v0.5 — GenExpr user functions must come before *all* statements, `Param`/`History` included. The
  `polyblep` function now sits at the top of the codebox. Read that log after every re-drag; a silent device
  with a clean log is a DSP or wiring problem, not a compile error. The C mirror is verified; the port is
  line-for-line.
- **Port verified in Live (2026-10-30 14:01)**: Freeze renders of the riff and laser clips from the v0.6
  device, measured with `proto/analyze_grid.py` at 138 bpm, match the C mirror rendered with the same dial
  values (Cutoff 0 %, Reso 79, Env Mod 39.4, Decay 24.7, Accent 79): identical f0 per note, per-note
  levels within 0.3 dB, peak trajectories within one analyser bin (21.5 Hz); overall RMS within ~1.3 dB.
  gen~ History semantics behave exactly like the read-at-top / write-at-end C code.
- To hand the core to the hardware: in Max, `gen~` ▸ *Export Code* (or Oopsy's bpatcher) on the device's
  gen~, or load `build/pocket303.gendsp` as `[gen @gen pocket303]` inside a Daisy wrapper that turns
  MIDI into the five inputs. See `hardware/pocket303/README.md` "Flash procedure B".

**Accepted by ear (2026-10-30 14:3x).** With "Acid Trojan" picked in the Sound menu René judged the
v0.6 device "close enough" to the Phoscyon track. Laser clip fix at the same time: non-accented laser
notes now carry velocity 90 (render.py and the Live clip) because the js and host.c treat velocity >= 100
as an accent, so the old clip accented every note. Measured on the C mirror, the change is small: the
3rd->2nd->1st harmonic trajectory is carried by Env Mod, accent adds about 1 dB and the extra "wow".
Still open: the sweep clip has no Cutoff ramp (draw 16 % -> 55 % over 28 bars in the clip envelope, or
ride the dial by hand as the record did), and the old "Pocket 303 v0.1" track still holds a broken
instance.
