# Pocket Kick — a bass drum with a 303 engine (Max for Live, gen~)

Third device of the Pocket line ([303](../pocket303/README.md), [101](../pocket101/README.md)). Two body
engines behind one set of controls:

- **Sine**: a sine with an exponential pitch sweep (TUNE 30–120 Hz, PITCH up to 5 octaves, P DEC 3–300 ms),
  click and noise mixed in after it — the 909 / Rytm family.
- **303**: a saw at the kick pitch into the Pocket 303's diode ladder with the resonance around
  self-oscillation; the ladder's cutoff carries the sweep and the click excites it. That is the acid
  "303 kick" trick (low note, high resonance, env decay) as a dedicated engine. CUT 50 % puts the ring on
  the fundamental, RES above ~60 % self-oscillates.

Then: NOISE layer with its own decay, asymmetric tanh DRIVE (even harmonics), TONE lowpass, DC block,
hold + decay amp envelope (HOLD 0–60 ms, DECAY 30–900 ms) split around the drive, ACCENT (velocity ≥ 100: +level and +drive), VOL. KEY = Track
makes the kick follow the note relative to C1 (for pitched 303-style kick lines); Fixed ignores pitch.

```
python3 m4l/pocketkick/build_device.py --install --version 0.1    # build/ + User Library/Devices/Pocket Kick/
cd m4l/pocketkick/proto && cc -O2 -o host host.c -lm && python3 render.py v2
uv run python m4l/pocketkick/proto/analyze_kick.py m4l/pocketkick/proto/renders_v2/techno.wav 136 1 4
```

Files: `pocketkick.genexpr` (voice), `proto/voice.h` (C mirror, `PARAMS` X-macro = the parameter list),
`proto/host.c` (2 lines: this voice + the shared `../../pocket101/proto/host_body.c`), `proto/render.py`
(four presets through a 4/4 pattern with an accent on 1 and a pitched pickup pair), `proto/analyze_kick.py`
(per hit: peak, time to -20 dB, f0 at 5/20/50/100/200 ms, centroid at 5 and 80 ms — the 303 analyser is
blind below 300 Hz), `build_device.py` (panel spec + presets; the slider-panel builder itself is the 101's
`device_patcher`, the container/gen~ plumbing the 303's).

## Parameters (gen~ names, 0..1; menus 0/1)

| section | control | gen~ | mapping |
|---|---|---|---|
| BODY | ENGINE | `body` | Sine / 303 |
| | KEY | `keytrack` | Fixed / Track (semitones from C1) |
| | TUNE | `tune` | 30 x 2^(2 TUNE) Hz |
| | PITCH | `sweep` | sweep depth, 5 x PITCH octaves (+ a 4 ms knock of 3 x CLICK octaves) |
| | P DEC | `sweep_dec` | 3 ms x 100^x |
| 303 | CUT | `cutoff` | ring at f0 x 2^((CUT-0.5) x 2) at the end of the sweep |
| | RES | `resonance` | k = 3.4 + RES (self-oscillation near 0.6) |
| AMP | HOLD | `hold` | 0 .. 60 ms at full level before the decay |
| | DECAY | `decay` | 30 ms x 30^x (time constant) |
| | ACCENT | `accent` | +50 % level, +30 % drive on accented hits |
| ATTACK | CLICK | `click` | impulse + 2 ms noise burst (into the ladder on the 303 engine) |
| | NOISE, N DEC | `mix_noise`, `noise_dec` | noise layer, 5 ms x 40^x |
| OUT | DRIVE | `drive` | tanh((1 + 9 DRIVE) x + bias) |
| | TONE | `tone` | 1-pole lowpass 200 Hz x 2^(6.6 TONE) |
| | VOL | `volume` | VOL² x 0.8 |

## Sound presets

| preset | engine | idea |
|---|---|---|
| Techno | Sine | 55 Hz (A1), 2.5-oct sweep in ~19 ms, hold 18 ms, decay 25, click 60, drive 55 — the device defaults |
| Acid 303 | 303 | 49 Hz saw, RES 85, 3-oct squelch over ~75 ms, click 50, drive 60, no noise |
| Rumble | Sine | 42 Hz, long decay, drive 70, dark tone |
| Boing | 303 | RES 100, ring half an octave above the fundamental, 3.5-oct sweep |

## Status (2026-10-30)

- **v0.2** (18:5x) after René's verdict on v0.1 ("too soft, no punch"; Acid 303 "not 303 enough"; "makes me
  feel nothing"): the amp envelope is now split around the drive (sqrt before, sqrt after) so the attack
  saturates while the tail still decays exponentially; a 4 ms three-octave "knock" rides on the pitch sweep
  (scaled by CLICK); a HOLD stage (0–60 ms) keeps the body up before the decay; the 303 ladder restarts on
  every hit (v0.1 hits differed because the saw phase and ladder state were never reset); the saw hits the
  ladder harder (0.8) with the 303's own make-up stage; defaults tuned to A1 (55 Hz, the fifth of the D riff),
  drive 55, decay 40 (25 since v0.3). Loaded via MCP on track "Pocket Kick v0.2" with the 4/4 clip; Live log clean. v0.1 stays
  on its own track for A/B until René deletes it. **v0.2 accepted by ear** (19:0x, "that's it").
- **v0.3** (19:1x): "shorter tail on that kick, I'm told" — DECAY default 40 -> 25 (time constant 70 ms; the
  Techno hit is -20 dB at ~250 ms instead of ~400), Acid 303 preset decay 50 -> 40. Loaded on track
  "Pocket Kick v0.3" (the legacy Remote Script cannot set device parameters, so a slider could not be moved in
  place); v0.2 stays for A/B. **Accepted** (19:2x, "short enough") — v0.3 is the current kick.
- v4 C renders: peaks -4.5..-5.3 dBFS, Techno RMS -11.5 dBFS (v0.1 was -16), knock at ~580 Hz for the first
  5 ms, f0 settles at 55 Hz by 100 ms, -20 dB at ~400 ms; Acid 303 squelch sweeps ~350 -> 170 -> 50 Hz over
  100 ms, hits now identical.
- v0.1 lessons: asymmetric drive + DC blocker overshoot (+5 dB); a 35 Hz DC blocker eats kick fundamentals (now
  3.5 Hz); envelope AFTER the drive makes a static, lifeless tone; envelope fully BEFORE the drive turns the
  drive into a compressor that rings into the next beat — hence the split.
- Noise differs sample-for-sample between C (xorshift) and gen~ (`noise()`); compare with NOISE and CLICK at 0.
