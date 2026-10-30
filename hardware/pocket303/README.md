# Pocket 303 — the hardware line for the 303 core

A desktop acid box: an Electrosmith **Daisy Seed3** behind eight pots, a MIDI In, a
line out and a USB-C port. It runs the **same gen~ core as the Max for Live device** in
`m4l/` (built in its own session): the gen~ patch is exported to C++ by Cycling '74's
**Oopsy** package and flashed to the Seed, so the plug-in and the box stay identical in
sound. Until that core lands, `firmware/` holds a small DaisySP placeholder that proves
the MIDI and audio paths on the bare board.

| File | What |
|------|------|
| [`oopsy/pocket303.json`](oopsy/pocket303.json) | Oopsy board definition: 8 pots, wave switch, gate LED, UART MIDI in. The gen~ patch's `[param cutoff]`, `[param resonance]`, … bind to the pots by name. |
| [`firmware/`](firmware/) | Placeholder voice in C++ (saw/square → ladder filter → VCA, accent, slide), `make` + `make program-dfu`. |
| [`tools/midi_test.py`](tools/midi_test.py) | Plays an accented, sliding acid line into the Seed over USB MIDI; `--reset` reboots it into DFU. |
| [`BOM.md`](BOM.md) | Parts with EU sources and prices (≈ €100 incl. the Seed). |
| [`wiring.md`](wiring.md) | Pin map, MIDI opto, audio out, power, panel layout. |

## Status (2026-10-30)

- Toolchain installed and verified on this Mac (section below); libDaisy v9 and DaisySP
  v1 built; Oopsy installed into Live 12's Max and its bundled libDaisy built.
- Placeholder firmware compiles (98.6 KB of the 128 KB internal flash).
- Oopsy pipeline verified from the command line with the Pocket 303 JSON: two gen~
  exports from Oopsy's own history (`simple`, `midi_input`) generate glue for the eight
  pots, the wave toggle, the gate LED and UART MIDI, and compile to 75 KB / 71 KB
  binaries (`node oopsy.js generate pocket303.json <cpp>`).
- **Not yet done on hardware:** the Seed3 was not on the bench during setup, so flashing
  and listening are still open, see "Flash procedure A". The in-Max route (bpatcher ▸ Browse ▸ JSON ▸ save) is the same
  script and still wants one click from you in Max.

## Toolchain on this Mac

| Piece | Where | How it got there |
|-------|-------|------------------|
| Arm GNU Toolchain 15.3.rel2 (`arm-none-eabi-gcc` 15.3.1, newlib-nano) | `/Applications/ArmGNUToolchain/15.3.rel2/arm-none-eabi/`, symlinked into `/opt/homebrew/bin/arm-none-eabi-*` | The Homebrew cask `gcc-arm-embedded` downloads this exact pkg but its installer needs `sudo`; the pkg payload was unpacked by hand to the cask's own install path and linked the way the cask does it. `brew install --cask gcc-arm-embedded` later (with your password) lands in the same place. Do **not** use the `arm-none-eabi-gcc` *formula*: it ships no newlib, so libDaisy cannot link. |
| `dfu-util` 0.11 | `/opt/homebrew/bin/dfu-util` | `brew install dfu-util` |
| libDaisy (v9.0.0) | `~/Daisy/libDaisy` (built: `build/libdaisy.a`) | `git clone --recursive https://github.com/electro-smith/libDaisy && make` |
| DaisySP (v1.0.0) | `~/Daisy/DaisySP` (built: `build/libdaisysp.a`) | `git clone --recursive https://github.com/electro-smith/DaisySP && make` |
| Oopsy (main, 5c9b1c8) | `~/Documents/Max 9/Packages/oopsy` (its own libDaisy v4 submodule built in `source/libdaisy/build`) | `git clone --recursive https://github.com/electro-smith/oopsy` in the Max 9 Packages folder, then `make` in `source/libdaisy`. Live 12.4's bundled Max is 9.1.4 and reads packages from `~/Documents/Max 9/Packages`. |
| `node` 24 | `/opt/homebrew/bin/node` | already present; only needed for the Oopsy command line (inside Max, Node for Max is used). |

Oopsy finds the compiler by looking in `/opt/homebrew/bin` and `/usr/local/bin`, which is
why the symlinks matter: Max does not inherit your shell `PATH`.

Rebuild everything from scratch:

```bash
cd ~/Daisy/libDaisy && make clean && make -j8 && cd ~/Daisy/DaisySP && make clean && make -j8
```

## Which Seed

René's bench board is a **Seed3** (confirmed 2026-10-30), which is what everything here
assumes. The firmware and the JSON are the same for every Seed revision; libDaisy detects the
board at boot (PH6 low = Seed3, PD5 low = Seed 1.2 / Rev 7, PD4 = Seed2 DFM, PD3 =
Seed 1.1, none = Rev 4) and brings up the right codec. Pinout and footprint are
identical between **Seed3** (USB-C, TAC5242) and **Seed 1.2** (micro-USB, PCM3060), which
is what `wiring.md` assumes. **Seed2 DFM** is electrically the same but a castellated SMT
module with another footprint, so it doesn't fit the perfboard build.

Datasheets and pinout CSVs: [Seed3](https://docs.daisy.audio/hardware/Seed3/),
[Seed](https://docs.daisy.audio/hardware/Seed/).

## Putting the Seed into DFU mode

Every upload (Oopsy or `make program-dfu`) needs the Seed's ROM bootloader:

1. Connect the Seed's USB-C to the Mac.
2. Hold **BOOT**, tap **RESET**, release **BOOT**.
3. `dfu-util -l` now lists `0483:df11` ("STM32 BOOTLOADER").

Once the placeholder or an Oopsy build is running, you can skip the buttons: send a MIDI
**System Reset** (`0xFF`) over USB or DIN and the firmware reboots into DFU by itself
(`uv run hardware/pocket303/tools/midi_test.py --reset`). Both the placeholder and
Oopsy's generated code do this.

## Flash procedure A — placeholder C++ (`firmware/`)

```bash
cd hardware/pocket303/firmware
make                  # bench build: pots ignored, everything on MIDI CC
make program-dfu      # Seed in DFU mode (see above)
```

For the finished box build with the pots enabled: `make POTS=1 program-dfu`.

What you get: a mono 303-ish voice on Audio Out L and R, last-note priority, velocity
≥ 100 = accent, overlapping notes = slide (no retrigger), the Seed's red LED follows the
gate. Controls (any MIDI channel):

| CC | Control | CC | Control |
|---:|---------|---:|---------|
| 74 | cutoff | 76 | accent amount |
| 71 | resonance | 77 | tuning (±1 semitone) |
| 12 | envelope amount | 5 | slide time |
| 75 | decay | 7 | volume |
| 70 | wave (≥ 64 = square) | 120/123 | all notes off |

Bench test from the Mac, no MIDI cable needed — the Seed shows up as **"Daisy Seed Built
In"** in Audio MIDI Setup and in Live's MIDI preferences:

```bash
uv run hardware/pocket303/tools/midi_test.py --list      # find the port
uv run hardware/pocket303/tools/midi_test.py --sweep     # two bars of acid with a cutoff sweep
```

Or route a MIDI track in Live to "Daisy Seed Built In" and play a keyboard. Over DIN/TRS
the same firmware listens on USART1 (D14), see `wiring.md`.

## Flash procedure B — the gen~ core via Oopsy

**In Max (the normal way).** Open the gen~ patch in the Max that Live launches (or
`~/Documents/Max 9/Packages/oopsy/examples/simple.maxpat` for a first try). Add the
Oopsy bpatcher (`File ▸ New From Template ▸ Oopsy …`, or an `[oopsy.pod]` object), then
in its panel pick the target: for the box, click **Browse** and choose
`hardware/pocket303/oopsy/pocket303.json`; for a bare Seed with audio only, `pod` works.
Put the Seed in DFU mode, then **save the patch** or click the bpatcher's compile button.
Oopsy exports the gen~ code, generates the glue for the pots/switch/LED/MIDI, compiles,
and uploads with `dfu-util`. Without a board attached you get Oopsy's harmless
"Error '74" after the compile and the `.bin` stays in
`~/Documents/Max 9/Packages/oopsy/source/build_<patch>_<target>/`.

**From the terminal** (same script Max runs; handy for CI and for checking the JSON):

```bash
cd ~/Documents/Max\ 9/Packages/oopsy/source
node oopsy.js /Users/rene/UhnTiss/uhntiss/hardware/pocket303/oopsy/pocket303.json \
     /path/to/exported_from_gen.cpp 48kHz block16          # generate + compile + upload
node oopsy.js generate <json> <cpp>                          # compile only
```

The exported `.cpp` is what gen~'s *Code Export* (or the bpatcher) writes; keep the
box's export next to the JSON in `oopsy/` once the core exists.

Inside the gen~ patch, the box's controls are `[param cutoff]`, `[param resonance]`,
`[param envmod]`, `[param decay]`, `[param accent]`, `[param tuning]`, `[param slide]`,
`[param volume]` (0…1, plus the aliases in the JSON such as `cut`, `res`, `env`),
`[param wave]` (0/1 from the toggle) and the gate LED is `[out 3 gate_led]`. MIDI
arrives as a signal of bytes the way Oopsy's `examples/midi_io.maxpat` shows; the
MIDI-in define in the JSON turns on the UART glue.

## Hand-off to the gen~ session

The sibling session only has to drop the exported cpp (or the .maxpat) here, then
`node oopsy.js pocket303.json <cpp>` or a save in Max is the whole flash step. If the
core needs a control the JSON doesn't have (a second switch, a CV in), add a component to
`pocket303.json`: Oopsy knows `AnalogControl`, `Switch`, `Switch3`, `Encoder`, `GateIn`,
`Led`, `RgbLed`, `GateOut`, `CVOuts`; `pin` is the Seed's D-number.
