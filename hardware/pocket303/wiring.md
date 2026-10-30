# Pocket 303 — wiring

Everything below is for a Daisy Seed3 (identical pins on Seed 1.2 / Rev 7). Pin numbers
are the **physical** header pins (1–40, counter-clockwise from the pin next to the USB
connector, D0), with the libDaisy/Oopsy name in brackets. Oopsy's board JSON uses the
D-number, so pot 1 = physical pin 22 = `A0 / D15` = JSON `"pin": 15`.

```
                           Daisy Seed3  (top view, USB-C at the bottom)
                    +3V3A  21 ●                      ● 20  AGND
       Cutoff   ->  A0/D15 22 ●                      ● 19  Audio Out R  -> (opt. 2nd jack)
       Resonance -> A1/D16 23 ●                      ● 18  Audio Out L  -> OUT jack
       Env mod  ->  A2/D17 24 ●                      ● 17  Audio In R   (unused)
       Decay    ->  A3/D18 25 ●                      ● 16  Audio In L   (unused)
       Accent   ->  A4/D19 26 ●                      ● 15  D14 USART1 RX <- MIDI in (opto)
       Tuning   ->  A5/D20 27 ●                      ● 14  D13 USART1 TX (MIDI out, unused)
       Slide    ->  A6/D21 28 ●       STM32H750      ● 13  D12
       Volume   ->  A7/D22 29 ●                      ● 12  D11
                    A8/D23 30 ●                      ● 11  D10
                    A9/D24 31 ●                      ● 10  D9
                   A10/D25 32 ●                      ●  9  D8
                       D26 33 ●                      ●  8  D7
                       D27 34 ●                      ●  7  D6
                   A11/D28 35 ●   [RESET]   [BOOT]   ●  6  D5
                       D29 36 ●                      ●  5  D4
                       D30 37 ●                      ●  4  D3
                    +3V3D  38 ●                      ●  3  D2
                      VIN   39 ●                      ●  2  D1  -> gate LED (+1k to GND)
                     DGND   40 ●       [USB-C]       ●  1  D0  <- WAVE toggle (to GND)
```

## 1. Grounds and power

- **Tie AGND (20) to DGND (40) outside the Seed** with a short wire at the header. The
  datasheet requires it for every application; without it the audio hums.
- **USB-C** (panel extension → the Seed's own USB-C) powers the box at 5 V and is the
  programming and USB-MIDI port. Nothing else needed.
- **Optional 9 V input:** barrel jack centre pin → 1N5819 (band towards the Seed) →
  VIN (39); 100 µF + 100 nF from VIN to DGND. VIN takes 4–17 V. USB and VIN may be
  connected at the same time (both are diode-OR'd on the Seed3).

## 2. The eight pots

Each pot: one end to **+3V3A (21)**, other end to **AGND (20)**, wiper to its ADC pin.
Clockwise should raise the value, so looking at the pot from the shaft side with the
lugs down: left lug = AGND, right lug = 3V3A, middle = wiper. Optional 100 nF from each
wiper to AGND if the wires are long.

| Pot | Physical pin | Name | JSON `pin` | firmware index |
|-----|-------------:|------|-----------:|---------------:|
| Cutoff    | 22 | A0 / D15 | 15 | 0 |
| Resonance | 23 | A1 / D16 | 16 | 1 |
| Env mod   | 24 | A2 / D17 | 17 | 2 |
| Decay     | 25 | A3 / D18 | 18 | 3 |
| Accent    | 26 | A4 / D19 | 19 | 4 |
| Tuning    | 27 | A5 / D20 | 20 | 5 |
| Slide     | 28 | A6 / D21 | 21 | 6 |
| Volume    | 29 | A7 / D22 | 22 | 7 |

Never feed the pots from 5 V: D17, D18, D21, D22 are not 5 V tolerant.

## 3. Wave switch and gate LED

- **Wave** toggle: common → D0 (pin 1), one side → DGND, the other side unconnected. The
  firmware enables the internal pull-up; closed = square, open = saw.
- **Gate LED**: D1 (pin 2) → 1 kΩ → LED anode, LED cathode → DGND. The Seed's own red
  LED (next to the USB) blinks with the gate too, so the panel LED is optional.

## 4. MIDI in (datasheet fig. 7.1–7.3, 3.3 V version)

```
  DIN pin 4 ──┬── 220 Ω ──── 6N138 pin 2 (anode)
              │                   │
  TRS ring  ──┘             1N4148 (cathode at pin 2, anode at pin 3)
                                  │
  DIN pin 5 ──┬────────────── 6N138 pin 3 (cathode)
  TRS tip   ──┘

  6N138 pin 8 (Vcc) ── +3V3D (38)       100 nF from pin 8 to DGND, close to the chip
  6N138 pin 5 (GND) ── DGND (40)
  6N138 pin 6 (Vo)  ──┬── D14 / USART1 RX (pin 15)
                      └── 270 Ω ── +3V3D
  6N138 pin 7 (Vb)  ── leave open
  DIN pin 2 (shield) and TRS sleeve ── 100 nF ── DGND   (do not ground them directly)
```

DIN and TRS are wired in parallel to the same two nets, so either connector works; do not
plug both in at once. TRS **type A**: tip = DIN 5, ring = DIN 4, sleeve = DIN 2.

The UART runs at 31250 baud; the firmware's `MidiUartHandler` default config is exactly
USART1 / RX D14 / TX D13, and Oopsy's generated code uses the same pins.

## 5. Audio out

```
  Audio Out L (18) ── 10 µF (+ towards the Seed) ──┬── 100 Ω ── jack TIP
                                                    └── 10 kΩ ── AGND
  jack SLEEVE ── AGND (20)
```

Same again from Audio Out R (19) for a second jack if you want stereo for the gen~ core;
the placeholder firmware sends the same mono signal to both. The Seed's output is
line level (~1 Vrms); the datasheet's OPA1652 stage (fig. 3.3) is only needed if you want
a 100 Ω, +9 V-rail "proper" line driver.

## 6. Panel layout (Hammond 1590BB, 120 × 94 mm face)

```
   ┌───────────────────────────────────────────────┐
   │  (MIDI DIN) (TRS)   (OUT 6.35)   (USB-C)  (9V) │   <- top edge, connectors
   ├───────────────────────────────────────────────┤
   │   CUTOFF     RESO      ENV MOD     DECAY       │   row 1, 26 mm pitch
   │     ◯         ◯          ◯           ◯         │
   │                                                │
   │   ACCENT     TUNE      SLIDE       VOLUME      │   row 2
   │     ◯         ◯          ◯           ◯         │
   │                                                │
   │        [WAVE saw/square]        ● gate         │
   └───────────────────────────────────────────────┘
```

Keep the Seed near the top edge so its USB-C lines up with the panel extension's cable,
and keep the opto and MIDI wiring on the opposite side of the board from the pot wipers.

## 7. Bench version (no box yet)

For the first flash you need nothing but the Seed on a breadboard, the USB cable, and a
3.5 mm jack on Audio Out L / AGND (or croc clips into a mixer). MIDI comes over USB from
the Mac (`tools/midi_test.py`) until the opto is built. The ADC pins float, so build the
bench firmware with `POTS=0` (the default) and control it with MIDI CCs.
