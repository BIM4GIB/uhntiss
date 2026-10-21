# Pocket 02 Auto validation — 2026-10-21

Built and tested on macOS / Apple Clang 21 / Ableton Live 12 Suite at 48 kHz.
Python remains development tooling; the device runs without it.

| Check | Result |
| --- | --- |
| C++ core / CMake | Pass |
| AddressSanitizer + UndefinedBehaviorSanitizer | Pass |
| Existing Python regression suite | 136 passed |
| Native Auto reserved-audio integration | 32/32 correct voices, zero extra/missing triggers, all nine sample-rate/gain cases |
| Rates / gains | 44.1 / 48 / 96 kHz × 0.5 / 1 / 2 gain |
| Quiet-room noise | Silence, low DC and seeded noise produce no hits; noise does not change the constructed groove |
| Capture timing / dynamics | Detector-to-loop timing error < one sample; velocity unchanged |
| Algorithmic Auto response | 10 ms analysis; first output within 12 ms of detected onset in regression |
| Universal Max external | arm64 + x86_64; ad-hoc signature verified |
| Max device container / audio wiring / controls | Pass |
| Actual Live Auto integration | 32 vocal attacks, all voices and relative timing; Clear, Undo and v3 blob restore pass |
| State migration | v3 retains all nine examples; v2 loops decode while obsolete voice centroids reset |

The reserved-audio regression builds an unsnapped mixed groove from **held-out
source attacks**, with source-file hashes and exclusions recorded in
`auto-model.json`. Gain variants of a held-out attack cannot enter training.
The tested performance is assembled from the same recording collection as the
training examples; it is **not** an independently recorded groove or a test of
unseen performers. Source attack association allows 25 ms. The largest observed
change relative to the reference threshold crossing was 15.91 ms at increased
gain. The separate < one-sample assertion compares the actual detected timestamp
with the captured loop; it does not claim that physical vocal onsets are known
to one sample. Full results: `auto-regression.json`.

The factory table has 125 examples, 14 causal features and three nearest
**distinct source attacks** per vote. Training uses only the named isolated
kick/snare/hat recordings. `auto-whole-takes.json` includes all eight natural-take
results, including weak results. Their legacy fixed-grid labels drift or omit
attacks, so those scores cannot establish a clean real-world recognition rate.
No mixed groove is used to train the factory table. The stronger results of the
legacy noncausal Python classifier are not attributed to this live engine.

The Live test feeds recorded vocal material through `play~` into the shipping
native external on a dedicated audio track. It checks the 32 resulting voices
and their relative offsets (within 1 ms of the headless detector), then checks
Clear, Undo and the actual `pattr` round trip. `auto-host-smoke.json` records the
result. This is stronger than the first release's pad-only host test, but is not
a physical microphone/interface round-trip measurement. Transport was stopped
after testing; the QA source player is excluded from the release.

The core suite also covers original unsnapped offsets, dynamics, replace,
overdub across either bar, empty takes, late classification at the seam, undo
at the seam, cancellation on stop/seek/meter change, event/history capacity,
fixed-voice response, teaching, host-phase continuity, malformed state rejection,
and queue ordering. Assertion totals include 100,000 SPSC messages, not 100,000
independent scenarios.

Hardware latency, physical footswitch behaviour, long-session stability, speaker
bleed, unfamiliar voices and very fast flams remain unverified. Minimum onset
separation is 35 ms. This release is a performance alpha, not a stage-readiness
or professional endorsement claim.
