# Pocket 04 · held-out and quiet-hit recovery — 2026-10-21

This is a **modest transfer improvement**, not a claim that fresh-take reliability
or stage performance is solved. Quiet hats gain a conservative recovery path;
confident primary voices retain priority. Python remains research-only.

## Whole-source held-out replay at 48 kHz

All 271 fixed reference attacks in eight untrimmed recordings are scored with
one-to-one matching within 25 ms. Each take is replayed through a separately
trained native model that excludes that source and every gain/resampling variant.

| Input gain | Correct before → after | Missed before → after | Wrong before → after | Extra before → after |
| --- | --- | --- | --- | --- |
| 1 | 241 → 243 | 14 → 12 | 16 → 16 | 14 → 14 |
| 0.5 | 226 → 228 | 28 → 26 | 17 → 17 | 17 → 17 |
| 0.25 | 213 → 220 | 46 → 39 | 12 → 12 | 18 → 16 |

The baseline is the exact Pocket03 source/model-training policy, recomputed at
48 kHz. `auto-heldout-pocket03.json` and `auto-heldout.json` retain every error,
source hash and model identity. CI gates correct, missed, wrong and extra counts
separately at each level. Existing reference labels were not changed to fit output.

The recovery rules were selected using these development recordings. Whole-source
training exclusion prevents waveform leakage; it does **not** make repeatedly
inspected recordings an untouched benchmark. Labels are signal-assisted acoustic
annotations, not independent listening ground truth. Quarter-level replay tests
amplitude robustness; naturally quiet articulation, new mic positions and voices
still need independent validation.

The entire `live_6_uptempo` source is excluded before deployment training or
augmentation. The shipped model hash equals that excluded fold's hash. Its fast
roll misses remain: at normal 48 kHz it gets 30/37 correct, with 7 misses and one
extra; at quarter level it gets 25/37 correct. Do not present the 271/271 old
all-data regression as this model's transfer accuracy.

## Runtime and timing

The primary model may recover a rejected attack using a separate voice/rejection
model. A second detector runs at 0.3 × Gate and accepts only hats with low noise
probability and raw velocity at most 0.22. Primary pending windows have priority;
a shared sample counter enforces 35 ms onset separation. No rhythm grid, gain
boost, added lookahead or deferred voice correction enters the audio path.

Both paths decide after the same **10 ms** window and retain the detected onset
and raw velocity. Context comes from the preceding 30 ms. Fixed voices and Teach
use their original single detector. The 17-feature state remains v4-compatible.

900 trees are stored: 300 normal-rate primary, 300 high-rate primary, and 300
recovery. Exactly one primary forest runs per attack. Above 48 kHz the primary
uses additional non-reserved calibration at 48/96 kHz; quiet and rejected-attack
recovery remain bounded. All trees have depth at most 12. The audio callback has
fixed storage and no heap allocation, locks, files, network or Python.

## Verification

- 100097 core assertions; AddressSanitizer and UndefinedBehaviorSanitizer pass.
- 136 existing Python tests and the Max packaging test pass.
- 300888 native/Python parity decisions across both rate models and all folds;
  the compiled engine returns a fingerprint of its actual exported header.
- 72 held-out capture windows preserve voices and velocities, with detected
  onset-to-loop error below 1.01 samples. Future-audio mutation leaves completed
  decisions identical. Decision windows never exceed 10 ms.
- Reserved 32-hit constructed groove passes at 44.1/48/96 kHz × .5/1/2 gain;
  silence, DC and quiet-room-noise regressions pass.
- Deployed model: 12 full-take rate/level cases, 24 original-rate capture windows,
  causality, and frozen per-metric regression gates pass. Normal-rate totals are
  264/271 at 44.1 kHz and 263/271 at 48 kHz because uptempo remains excluded.
- Universal arm64/x86_64 build, ad-hoc code signature and container checks pass.

The four 48 kHz desktop benchmarks have 64-sample p99 times of 0.055–0.063 ms.
One observed maximum is 3.079 ms, above the 1.333 ms block budget. CPU time has
increased; an unchanged analysis window is not a hard real-time scheduling or
hardware round-trip latency guarantee. See `auto-benchmark.json`.

## Live host validation

Pocket04 passed in Live at 48 kHz on the entire 14.4-second uptempo source at
quarter level. A 30 BPM host supplies a 16-second capture window; the source
plays at its original speed. All 31 native detections captured with matching
voices and velocities; relative onset error was 0.000160 samples. Clear, Undo and
v4 state restore passed. These are 31 native predictions, **not** 31 correct
reference attacks. `auto-host-smoke.json` retains events and source/model/binary
hashes. Pocket03's historical result is in `auto-host-smoke-pocket03.json`.

Hardware round-trip latency, sustained stage sessions, speech/bleed rejection,
new performers and overlapping vocal classes remain unverified. No performer
endorsement is implied.
