# Pocket 101 — an SH-101 style bass for Live (Max for Live, gen~)

The bass to go with the [Pocket 303](../pocket303/README.md): one VCO with the SH-101's source mixer
(saw / PWM pulse / sub-oscillator / noise), an IR3109-style 24 dB OTA filter, ADSR, a free-running LFO
and portamento. Same build recipe as the 303: the DSP lives in one GenExpr file with a C mirror for blind
design; the device is generated, version-stamped and installed by a script.

```
python3 m4l/pocket101/build_device.py --install --version 0.1     # build/ + User Library/Devices/Pocket 101/
cd m4l/pocket101/proto && cc -O2 -o host host.c -lm && python3 render.py v1   # C renders of the presets
```

Files: `pocket101.genexpr` (the voice), `proto/voice.h` (identical C mirror; its `PARAMS` X-macro is the
single list of parameters), `proto/host.c` (two lines: this voice + the generic `proto/host_body.c`, which reads params by name from that
table and is shared with the other Pocket devices),
`proto/render.py` (one-bar D bassline through every Sound preset), `build_device.py` (panel + presets; its `device_patcher(gen, version, title=, varname=, js=, sections=, presets=,
prefix_labels=)` is the slider-panel builder the other Pocket devices import; the container/gen~ plumbing
comes from `../pocket303/build_device.py`; the 303's mono note js is installed as `pocket101_notes.js`).

## Signal path

```
midiin -> midiparse -> js (mono stack, last-note priority, legato flag, trig counter) -> gen~
gen~:  LFO (tri/sq/S&H/random, 0.1..30 Hz, free running)
       portamento (off / auto = legato only / on, RC 5 ms..2 s) -> pitch (+range, tune, LFO)
       VCO: polyBLEP saw; pulse = saw(p) - saw(p + pw) (PWM from LFO / manual / env);
            sub from a divided phase (-1 oct square, -2 oct square, -2 oct 25 % pulse); white noise
       mixer (linear sliders) -> x0.5 -> 4 x [OTA stage: a += g (tanh(in) - tanh(a))], 2x oversampled,
            resonance k = 4.3 x RES fed back from the half-sample-averaged 4th stage (self-oscillates above ~93 %)
       cutoff 20 Hz x 2^(9.5 FREQ), + ENV up to 8 oct, + LFO up to +-3 oct, + KYBD tracking (1 = 100 %)
       ADSR: A 1.5 ms..3 s, D/R 2 ms..12 s (exponential, retriggers on every note); VCA = env or gate
       make-up tanh(y (1 + 0.25 k)) -> DC blocker -> VCA -> VOL^2
```

Inputs are the Pocket 303's five (note, held, accent, legato, trig) so the js is shared. The 101 ignores
velocity, like the original keyboard.

## Parameters (gen~ names; sliders 0..1, switches as listed)

| section | control | gen~ param | mapping |
|---|---|---|---|
| VCO | RANGE menu | `oct_range` 0..3 | 16' 8' 4' 2' = -12 / 0 / +12 / +24 semitones |
| | PWM menu | `pwm_src` 0..2 | LFO / MAN / ENV (what drives the pulse width) |
| | MOD | `lfo_vco` | LFO -> pitch, +-7 x MOD² semitones |
| | PW | `pw` | 0 = square, 1 = 5 % pulse (MAN); modulation depth in LFO/ENV modes |
| MIXER | PULSE SAW SUB NOISE | `mix_pulse mix_saw mix_sub mix_noise` | linear levels (noise x0.7) |
| | SUB TYPE menu | `sub_type` 0..2 | -1 oct square / -2 oct square / -2 oct 25 % pulse |
| VCF | FREQ RES ENV MOD KYBD | `cutoff resonance env_vcf lfo_vcf kybd` | see signal path |
| ENV | VCA menu | `vca_mode` 0..1 | ENV / GATE |
| | A D S R | `attack decay sustain release` | exponential time ranges above |
| LFO | WAVE menu, RATE | `lfo_wave` 0..3, `lfo_rate` | Tri / Square / S&H / Random; 0.1 x 300^RATE Hz |
| PORTA | MODE menu, TIME | `porta_mode` 0..2, `porta` | Off / Auto / On; 5 ms x 400^TIME |
| OUT | TUNE, VOL | `tuning` (-12..12 st on the panel), `volume` | |

Everything the Daisy pots will need is 0..1; the switches are small ints, as Oopsy likes them.

## Sound presets (umenu, writes every slider and menu)

| preset | idea |
|---|---|
| Sub bass | 16', saw 60 + sub 100, FREQ 35, RES 10, slow-ish decay with sustain 30 |
| Acid bass | 8', saw 100 + sub 70, FREQ 30, RES 55, ENV 55, D 55, porta Auto 30 — also the device defaults |
| Pulse bass | PW 40 % pulse + -2 oct sub, FREQ 40, RES 25, sustain 20 |
| Zap | 4', RES 85, ENV 90, D 35 — the resonant sweep |

`proto/render.py` holds the same four as gen~ values (`SOUNDS`); keep both in sync.

## Status (2026-10-30)

- v0.1 built, installed and loaded through the Ableton MCP onto track "Pocket 101 v0.1" (18:18). Live's
  log shows no "Message from Max" lines for the load, so the codebox compiled and every param is known.
  Clip "101 test bass (D)" (one bar, same notes as `render.py`) is in slot 0. **Accepted by ear**
  (18:3x, "sounds right" on the default Acid bass next to the 303 riff). There is no SH-101 emulation
  installed to measure against (only Phoscyon 2 and Softube Model 84 among the plug-ins), so the tuning is
  physics + ear; retune by request.
- C renders of the four presets are clean (peaks -5..-7 dBFS, legato pair glides, zap sweeps 3.8 kHz -> 300 Hz
  in ~50 ms). The 303's `analyze_grid.py` has a 300 Hz floor and is blind to bass fundamentals; a bass-aware
  analysis (sub/fundamental ratio, decay time, resonance peak) is still to write if measuring is needed.
- Not modelled: arpeggiator / sequencer (use Live's), bender, the noise LFO's exact colour. Noise differs
  sample-for-sample between C (xorshift) and gen~ (`noise()`); compare renders with NOISE at 0.

GenExpr names avoided on purpose (they are gen operators/constants): `e`, `sub`, `wrap`, `mix`, `gate`,
`noise`, `sah`, `latch`, `slide`, `wave`, `fract`; see the 303 README for the function-order rule.
