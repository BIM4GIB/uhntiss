# Pocket 06 validation

Current enrollment results and limitations are in
[the shared contract](../../docs/pocket-integration.md#current-measurements-2026-10-25).
Reports: `enrollment-avp-48000.json`, `enrollment-avp-44100.json`,
`enrollment-negatives.json`, `enrollment-benchmark.json`.

Native tests cover v2/v3/v4 migration, maximum v5 state, malformed imports,
named MIDI voices, exact 10 ms audible / 80 ms stored deadlines, 50 ms rolls,
seam tails, clear/undo/restore/rate changes, no late retrigger and zero audio-path
allocations. ASan/UBSan pass. Python verifies native spectral parity and
quantized classifier/rejection parity. JS checks the actual PROFILE int handler.

## Pocket 06.1 host verification — 2026-10-26

Live 12.4.3 / bundled Max 9.1.4, 48 kHz. An actual PROFILE click loaded four
voices with 30 personal rejection examples; the audio thread acknowledged it.
The host harness captured six pad hits, cleared/undid/restored the v5 pattr
blob (7,262 values), then replayed custom note 37 at beat 0.127891. This checks
host serialization and native MIDI-outlet replay, not acoustic classification.
The fixed 10/80 ms deadlines and roll timing remain native-core test results.

The first real click exposed Max File truncation: a 73,668-byte JSON returned
32,767 characters while moving the file position to 73,668. Pocket 06.1 reads
chunks below this per-call limit. `profile-reader-runtime.json` reproduces the
old failure and the complete 7,229-value output in Max's actual JS runtime.
The mock now models this cursor jump and ordinary short reads.

MIDI receiver verification passes: a separate MIDI effect received the six
controlled notes at velocities 57/67/77/88/98/108 and custom note 37 at velocity
47 from restored-loop playback. `profile-host-smoke-061.json` includes both sides.
A fresh instance also passed with the final deferred port/configuration query;
`profile-startup-smoke-061.json` records its populated MIDI destination list.
These are pad-driven host tests, not fresh-take accuracy or round-trip latency
measurements. Pattr blob get/set was tested; saving and reopening the whole
Live Set was not exercised during this session. The dedicated QA and reader patches are excluded
from the playable bundle. JS/native resource names are versioned for coexistence.

Factory model hashes and reserved sources are unchanged from Pocket 05. The
following historical validation remains relevant to the fallback factory path;
it is not evidence of cross-performer or open-mic stage reliability.

# Pocket 05 · quiet transient recovery — 2026-10-21

This release recovers some quiet kicks, snares and hats at 48 kHz. **The hard
fast-roll kick/snare misses are still unresolved.** Test labels stay frozen,
held-out audio stays outside deployment training, and the decision window stays
10 ms. Python remains research-only.

## Whole-source held-out replay

271 fixed reference attacks in eight complete recordings, scored one-to-one
within 25 ms. Each native fold excludes its test source and every derivative.
The additional transient model also permanently excludes `live_6_uptempo` in
every fold, before audio is read. Primary and recovery model bytes are unchanged
from Pocket04. The shipped combined model matches the uptempo-excluded fold on
the development Mac; export parity and embedded fingerprints are checked on each
platform, without assuming training is bit-identical across platforms.

Development Mac results:

| 48 kHz input gain | Correct 04 → 05 | Missed 04 → 05 | Wrong 04 → 05 | Extra 04 → 05 |
| --- | --- | --- | --- | --- |
| 1 | 243 → 243 | 12 → 12 | 16 → 16 | 14 → 14 |
| 0.5 | 228 → 228 | 26 → 26 | 17 → 17 | 17 → 17 |
| 0.25 | 220 → 225 | 39 → 34 | 12 → 12 | 16 → 13 |

The five newly recovered quarter-level references are bb84 hat 10.67274s and
kick 13.2419s, live_1_boombap snare 5.838s, and uptempo hats 7.564s and 9.098s.
Linux retraining recovers the same five references, from 219 to 224 correct;
misses fall 40 to 35 and extras 16 to 13. Its historical Pocket04 model already
missed the quiet uptempo kick at 2.156s. Normal and half-level totals match the
Mac. These are platform-dependent training results, not a changed release model.

CI selects a frozen Pocket04 baseline by the eight primary-model fingerprints,
then requires an identical score from a paired native run with only the new
transient path disabled. Unknown model fingerprints fail. It gates each take,
rejects newly missed references, checks that every remaining wrong/extra event
corresponds to an existing one within 25 ms, and requires five recovered quiet
references and three fewer extras. Both historical reports are retained in
`auto-heldout-pocket04.json` and `auto-heldout-pocket04-linux.json`; the latter
comes from [Pocket04 CI run 39541793161](https://github.com/BIM4GIB/uhntiss/actions/runs/39541793161).
`auto-heldout.json` and `auto-heldout-linux.json` retain each host’s errors, source
hashes, model identities, paired scores and capture checks. Failed CI runs also
retain their evidence.

The permanent uptempo holdout remains **30/37 correct at normal level**, with
7 misses and 1 extra. At quarter level it improves **25 → 27/37**, with misses
11 → 9, wrong 1 → 1, and extras 5 → 3. Unresolved normal-level K/S references are
2.313K, 4.604S, 7.148S, 7.242K, 9.573S and 9.671K. Their first windows are rejected;
broader acceptance adds false hits. Some references lie within voiced bodies,
so independent listening annotations and separate roll-calibration examples are
needed before claiming the intended articulation is understood. The original
references remain frozen, including these misses.

These recordings have informed engineering choices. Source exclusion prevents
waveform leakage; it does not make this a blind test. Labels are signal-assisted,
not independent human ground truth. Gain reduction tests level robustness, not
the full variety of naturally quiet articulation.

## Runtime and timing

At exactly 48 kHz, a third fixed-storage tracker measures rises within six
frequency bands. Its 10 ms descriptor adds six contrasts against preceding band
energy with a 12 ms decay. A 200-tree model must pass a 0.7 K/S or 0.8 hat vote,
and raw velocity must be at most 0.22. Primary pending windows retain priority.
All paths share a sample-based **35 ms accepted-onset separation**. Experiments
with broader acceptance and shorter separation added extras and were rejected.

Each path decides after **10 ms from its detected onset**, with no future
lookahead, resampling buffer, gain boost, rhythm grid or deferred correction.
Capture preserves that onset and raw velocity. Recovery can detect an acoustic
attack earlier than the old gate; sub-sample capture accuracy does not mean a
sub-sample estimate of the true acoustic onset. Fixed voices, Teach and state v4's
17-feature profile are unchanged. Other sample rates retain their Pocket04 path.

On shared correct deployed-model attacks, 5/9/8 detected onsets change at
normal/half/quarter level, all earlier (up to 16.4/21.4/22.3 ms). Mean absolute
source-label error improves 2.59→2.46, 3.75→3.63 and 5.03→4.70 ms respectively.
This is not a promise of identical Pocket04 threshold times; see
`auto-onset-timing.json` and the live-path A/B renders.

There are 1100 stored trees: the existing 900 (one primary rate model runs per
attack), plus 200 transient trees of maximum depth 14. The audio callback has
no heap allocation, locks, files, network or Python. CPU cost increases; unchanged
algorithmic delay is not a hardware round-trip or scheduling guarantee.

## Checks

- 100097 core assertions pass, including AddressSanitizer/UndefinedBehaviorSanitizer.
  Separate test frames avoid accumulating every test engine's fixed buffers in
  one Debug/ASan `main` stack frame.
- 136 Python tests and the Max container test pass.
- 405013 native/Python parity decisions across all fold models and probes.
- 72 held-out capture windows preserve voices, velocity and detected onset within
  1.01 samples. Mutating future audio leaves completed decisions identical.
- The reserved 32-hit constructed groove passes at 44.1/48/96 kHz × .5/1/2 gain.
  Silence, DC and quiet-room-noise checks pass.
- Deployed regression passes 12 full-take rate/level cases. At 48 kHz, quarter-level
  correct hits improve 239 → 248 and half-level 254 → 260. Seven of these eight
  takes train the deployed model; these are regression results.
- 27 frozen synthetic breath-shaped noise controls produce **19 false triggers
  before and after**, with no new trigger locations. These signals never train
  the model. This is a no-regression stress check, **not zero breath errors** or
  a human-breath benchmark. See `auto-noise-controls.json`.
- Universal arm64/x86_64 external and ad-hoc code signature verified.

## Live host validation

Pocket05 passed in Live at 48 kHz on the entire 14.4-second uptempo recording at
quarter input level. A 30 BPM host supplies a 16-second two-bar capture window;
the source plays at its original speed. All **31 native predictions** capture
with matching voices and velocities, relative onset error below 0.001 samples,
and passing Clear, Undo and v4 restore. Acoustic accuracy is 27 correct against
37 references; do not call this 31 correct hits. `auto-host-smoke.json` records
source, combined-model and binary hashes. The Pocket04 host report is archived.

Hardware round-trip latency, sustained stage use, actual breath/speech/bleed
rejection, overlapping vocal classes and new performers remain unverified.
No performer endorsement is implied. See `auto-benchmark.json` for observed CPU
costs; desktop measurements are not hard real-time guarantees.

The four 64-sample desktop measurements have p99 block times of 0.076–0.086 ms.
One observed maximum is 3.861 ms, above the 1.333 ms block budget. The others are
0.367–0.747 ms. This records the observed scheduling spikes rather than claiming
that an unchanged 10 ms analysis window guarantees every host deadline.
