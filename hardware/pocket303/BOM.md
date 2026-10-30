# Pocket 303 — bill of materials

Prices are approximate EUR incl. VAT, checked 2026-10-30, rounded up. Shop links are
the usual EU DIY sources; Tayda (Thailand) is cheapest for pots/knobs/jacks/enclosures
if you can wait two weeks. Total lands around **€100**, of which ~€45 is the Seed.

## The brain

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 1 | **Daisy Seed3**, 65 MB, with headers | 1 | USB-C, TAC5242 codec (32-bit/192 kHz), pin-compatible with the older Seed. $29.99 at daisy.audio, rising to $34.99 on 1 Nov 2026; EU resellers (Schneidersladen, Thonk, Electrokit, Exploding Shed) listed the previous Seed at €40–50 and are restocking with the Seed3. | [daisy.audio/products/seed3](https://daisy.audio/products/seed3), [schneidersladen.de](https://schneidersladen.de/en/electrosmith-daisy-seed-65mb-w-headers) | 45 |
| 2 | Female pin header 1×20, 2.54 mm | 2 | Socket the Seed on the main board so it can be pulled for other projects. | Reichelt `BL 1X20G8 2,54`, Mouser | 1 |

*Already own a Seed?* **Daisy Seed 1.2 / Rev 7** (micro-USB, PCM3060) is the same pinout and
works unchanged; the firmware detects the revision. **Seed2 DFM** is a castellated SMT
module with a different footprint, so it does not fit this perfboard build (electrically
everything else still applies).

## Front panel

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 3 | Potentiometer **B10K** (10 kΩ linear), Alpha 9 mm `RD901F` or 16 mm `RV16AF` | 8 | Cutoff · Resonance · Env mod · Decay · Accent · Tuning · Slide · Volume. 9 mm fits a 1590BB; 16 mm wants a 1590XX. | Musikding, Thonk, Tayda | 11 |
| 4 | Knobs to match the pot shaft (6 mm D-shaft or 18T knurled) | 8 | Buy pots and knobs from the same shop so the shafts match. | Musikding, Tayda | 7 |
| 5 | Mini toggle switch SPDT (ON–ON) | 1 | Wave: saw / square. Only the common and one side are used. | Musikding, Tayda | 2 |
| 6 | LED 3 mm + 1 kΩ resistor | 1 | Gate LED on D1. | any | 1 |

## MIDI in (DIN and TRS type A in parallel, per the Seed3 datasheet §MIDI)

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 7 | DIN-5 180° panel socket | 1 | MIDI In. | Reichelt `MAB 5 SH`, Musikding | 2 |
| 8 | 3.5 mm TRS panel jack (stereo) | 1 | MIDI In, TRS type A (tip = DIN 5, ring = DIN 4). Wired in parallel with the DIN; use one at a time. | Thonk, Tayda `PJ-301M` | 2 |
| 9 | **6N138** optocoupler + 8-pin DIP socket | 1 | The classic MIDI-in opto; works at 3.3 V with the 270 Ω pull-up. | Reichelt, Mouser | 1 |
| 10 | 220 Ω, 270 Ω, 1N4148, 3× 100 nF | — | 220 Ω in series with the LED, 1N4148 reverse across it, 270 Ω pull-up on the opto output, 100 nF decoupling + shield caps. | any | 1 |

## Audio out

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 11 | 6.35 mm mono panel jack (TS) | 1 (+1 opt.) | Main out from Audio Out L; a second jack on Audio Out R if you want a stereo pair for the gen~ core. | Neutrik `NMJ4HC-S`, Lumberg `KLBM 3`, Tayda | 3 |
| 12 | 10 µF (bipolar/film or 2× 22 µF back-to-back electrolytic), 100 Ω, 10 kΩ per output | — | AC coupling, output protection, bleed. The datasheet's op-amp line stage is optional; the bare output drives a mixer or headphones fine. | any | 1 |

## Power

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 13 | Panel-mount **USB-C extension** (female panel socket → male plug, 20–30 cm) | 1 | One port on the panel does three jobs: 5 V power, flashing (DFU), USB MIDI from the Mac. | Amazon, AliExpress | 7 |
| 14 | DC barrel jack 2.1 mm + 1N5819 Schottky + 100 µF + 100 nF (optional) | 1 | Stand-alone power from a 9 V centre-negative pedal supply into VIN (4–17 V). Seed3 VIN is already reverse-protected; the Schottky is belt and braces. | Musikding | 2 |

## Mechanics

| # | Part | Qty | Notes | Source | ≈ EUR |
|---|------|-----|-------|--------|------:|
| 15 | **Hammond 1590BB** (120 × 94 × 34 mm) aluminium box | 1 | 8 knobs in two rows of four, connectors on the top edge. Choose 1590XX (145 × 121 × 39) for 16 mm pots and more elbow room. | Musikding, Reichelt, Tayda, RS | 14 |
| 16 | Perfboard ~80 × 60 mm, M3 standoffs, hookup wire, heat-shrink | — | | Reichelt | 4 |
| 17 | 100 nF ×8 (pot wipers), 10 µF | — | Optional RC on each wiper kills ADC noise on long wires. | any | 1 |
| 18 | Rubber feet, knob labels | — | | | 1 |

**Sum:** ≈ €106 with a Seed3 bought in the EU, ≈ €60 if you already have a Seed.

## Not needed

- No level shifters: everything on the panel runs at 3.3 V. Note that D17, D18, D21, D22 and
  D23 are **not** 5 V tolerant, so pots must be fed from 3V3A (pin 21), never from 5 V.
- No ST-Link: flashing goes over USB DFU (the Seed's own bootloader) with `dfu-util`.
- No MIDI out/thru for v1. If you add one later: UART TX (D13) → 10 Ω → DIN pin 5, 3V3 → 10 Ω → DIN pin 4 (datasheet fig. 7.4).
