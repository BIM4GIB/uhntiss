# Pocket 03 · full real-take validation — 2026-10-21

The eight original, untrimmed personal takes contain **271 audited attacks**.
Pocket 02 matched 169 voices correctly, missed 8, chose 94 wrong voices and added
118 triggers. Pocket 03 matches **271/271**, with **0 missed, wrong or extra hits**
at the original recording rate and level. Every error timestamp from the earlier
engine is retained in `auto-real-takes.json`.

These takes now train the personal factory model. This is a corpus regression,
not unseen-performance accuracy. Labels came from signal-assisted waveform and
spectral inspection, not independent human listening. They retain distinct
bursts and ghost hits and omit repeated triggers inside a continuous sound. The
reference is not a quantized beat grid. One-to-one association allows 25 ms.

## Transfer and limits

Native leave-one-whole-take-out development replay excludes every variant of
the evaluated take: **239/271 correct**, 14 missed, 18 wrong voices and 16 extra
triggers. Each fold compiles and runs its own model, including its own onset
recovery decisions. Hyperparameters were developed against this same collection;
this is not an untouched benchmark or an unseen-performer test.

At the same level after resampling, the full-corpus score is **269/271 at 48 kHz**
(1 miss, 1 wrong voice, 1 extra) and **266/271 at 96 kHz** (3 misses, 2 wrong voices,
3 extras). Halving input level produces 253–256 correct hits, with 14–16 misses;
the fixed gate can lose quiet attacks. Doubling it produces 263–268 correct hits.
All nine rate/gain cases and their individual errors remain in the report.
Adjust Input/Gate during soundcheck; this release does not claim gain invariance,
speaker-bleed cancellation, speech rejection or stage reliability.

## Timing, state and build checks

| Check | Result |
| --- | --- |
| C++ core / ASan / UBSan | Pass; 100095 assertions, including 100000 queue messages |
| Existing Python suite | 136 passed; local TCP fixtures require loopback access |
| Reserved constructed groove | 32/32 voices, no missing/extra hits at 44.1/48/96 kHz × 0.5/1/2 gain |
| Full-take capture | 24 successive two-bar windows cover all eight takes; detected onset to loop error < one sample; velocity unchanged |
| Causality | Replacing every sample after a cut leaves earlier completed decisions and features identical |
| Decision window | 10 ms, unchanged; preceding 30 ms contributes context without lookahead |
| Model export | All 5135 development feature rows match Python/native decisions exactly, including lossless shared-tree export |
| State | v4 round trip; v2/v3 loops preserve timing while incompatible profiles reset |
| Device build | Universal arm64/x86_64; ad-hoc signature and container checks pass |

Reserved isolated attacks and every candidate within 200 ms are excluded at all
training gains. Random context augmentation uses only the remaining isolated
attacks. Constructed tests were used during development and remain regressions,
not independent validation. Python is absent from the running device.

The model has 300 trees with maximum depth 12. Identical leaves and subtrees are
shared without rounding thresholds or probabilities. Runtime inference, detector
history, synthesis and loop storage have fixed bounds, with no heap allocation,
locks, network or file access in the audio callback.

The real-audio benchmark with a full 512-event loop processed 14.4 seconds of
snare-heavy audio in 0.198 seconds and accepted 45 live hits. The 99th-percentile
64-sample block was 0.049 ms; the observed maximum was 3.037 ms, exceeding that
block's 1.451 ms budget on this desktop run. This is not a hard real-time or
hardware round-trip latency guarantee. See `auto-benchmark.json`.

## Live host check

Pocket 03 passed the full-take check in Live at 48 kHz: all 45 voices in the
14.4-second snare-heavy take matched, with relative onset error below one sample.
The harness captures the whole take in two bars at a 30 BPM host; the source
plays at its original speed. Clear, Undo and v4 saved-state restore also passed.
`auto-host-smoke.json` records the actual events, expected events and source,
model and binary hashes. This verifies host capture, not hardware round-trip latency.

An earlier attempt reported a mismatch while collecting per-hit UI status. The
final check verifies the captured event list instead; its status collector also
reported no skipped messages. The historical Pocket 02 host check remains in
`auto-host-smoke-pocket02.json`.

Hardware latency, physical footswitches, long sessions, new microphones/voices,
and attacks closer than 35 ms remain unverified. No performer endorsement is
implied.
