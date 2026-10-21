# Milestone 01 validation — 2026-10-21

Validated locally on macOS with Apple Clang 21 and Ableton Live 12 Suite, 48 kHz.

| Check | Result |
| --- | --- |
| CMake portable core and tests | Pass |
| CMake universal Max external | Pass, arm64 + x86_64, ad-hoc signature verified |
| Direct macOS build and device packaging | Pass |
| AddressSanitizer + UndefinedBehaviorSanitizer | Pass |
| Python research/legacy regression suite | 136 passed |
| Generated AMXD container, native audio wiring, parameters and blob binding | Pass |
| Live host integration | Captured six timed hits; Clear, Undo and blob restore passed |
| Visual check in Live | Controls, voice selection, teaching buttons and fractional event display render |

The core suite covers unsnapped 30.5 ms timing offsets, velocity retention,
replace, overdub across either bar, empty passes, seam-delayed classification,
undo during the seam, cancellation on stop/seek, meter rejection, event/history
capacity, three supported sample rates, fixed-voice override, teaching, and
host-phase continuity when control messages are sparse. It checks playback
within one sample across a tempo change and exercises 100,000 messages through
the SPSC queue. The printed assertion total includes those queue checks; it is
not a count of independent test scenarios.

A 512-event loop rendering ten seconds of audio consumed approximately 0.143 s
of wall time in the native benchmark on this development Mac (1.43% of real
time). That is a headless engine observation, not a CPU guarantee for other
machines or a measured hardware round trip. Detection decisions arrive about
8 ms after the stored threshold-crossing onset; audio buffers add latency.

The host smoke test ran in a dedicated test track and used generated pad
commands. It verifies capture duration, actual audio output on Live's meters,
clear/undo and the saved-state blob round trip. It does not establish mic
recognition quality, a saved Live Set reload on another computer, Intel-host
runtime behaviour, physical MIDI footswitch behaviour, long-session stability,
or stage readiness. The native Auto classifier's weak fixture results are
reported in README and the research evaluator rather than hidden by the older
Python pipeline's better offline scores.
