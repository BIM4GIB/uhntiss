# UHNTISS Pocket 06.1 · personal voices and delayed loop decisions

A local voice-to-drum instrument and two-bar looper for Ableton Live. Native C++
handles detection, synthesis, transport and recording. Max for Live supplies the
host connection and controls. Python remains an offline research tool.

**Performance research alpha, not stage-qualified.** Pocket 06 accepts 2–16
named sounds mapped to MIDI notes, with up to 30 enrollment examples each.
The performer hears Auto at the same 10 ms deadline; the loop can revise the
voice using up to 80 ms of causal audio. Neither path moves the detected onset
or changes its measured velocity. Python builds profiles offline only.

On AVP's 25 performers at 48 kHz, the new three-voice profile scores 70.5% live
and 75.2% in the loop on identical matched candidates, with rejection counted
as wrong. The fourth voice costs about one point in committed core accuracy at
48 kHz; it improves it at 44.1 kHz. Personal noise enrollment helps one hum test
but transfers poorly in the reverse direction. Real speech/breath and expert
roll coverage remain release gates. See [the measured contract](../../docs/pocket-integration.md).

## Play

Requires macOS 11+, Ableton Live with Max for Live, and a mono mic input. The
provided external is universal Apple Silicon/Intel. The portable core also builds
on Linux; there is no Windows Max external in this release.

1. Keep `UHNTISS Pocket.amxd`, `pocket061-ui.js`, `pocket061-profile.js` and `uhntiss.pocket061~.mxo` together.
   Drag the `.amxd` onto an **audio track**. No API key, bridge, Python, Node server,
   network connection or downloaded kit is needed to play.
2. Select your mono mic channel in Live; set the track's Monitor to **In**. The
   device produces drum sound; it does not pass the dry mic through. Use your
   normal low-latency interface setup and headphones while using an open mic.
3. Leave **Input Voice → Auto**. Beatbox kick (`B`), snare (`PF`) and hat (`TS`)
   in one performance; no mode switching between hits.
   Adjust Input and Gate until quiet room noise stops triggering and soft hits
   still register. Use the kick/snare/hat pads to test the kit without a mic.
4. Run Live in **4/4**. Press **Record 2 Bars** before a downbeat. The display moves
   from armed to recording at the next bar; play for two bars. The new loop starts
   at the following seam. If armed with transport stopped, start Live and watch
   the armed/recording display for the actual capture boundary.
5. Select **Overdub** to add another mixed pass, still in Auto.
   Original hits keep their phase even if the pass starts on the other bar.
   **Replace** swaps in a new take. The old loop plays while you make either take.
6. **Undo** cancels an armed/in-progress take, or restores the previous committed
   loop. **Clear** is undoable. There are 16 loop-history steps. Empty passes leave
   your loop intact. Undo history lasts for the device instance, not across reloads.

Record, Undo, Clear, mode, voices, pads and sound controls are Live parameters.
Use Live's MIDI Map or Key Map to assign record/undo to footswitches or keys. The
pads use fixed velocity 0.85 when remotely mapped; this is **not** a velocity-aware
MIDI instrument. Mic dynamics are retained. MIDI/keyboard control arrives at a
block boundary; it is not a sample-offset MIDI API.

**LIVE** toggles the sound from new input/pad hits without stopping capture.
**LOOP** toggles loop playback. Tone, Decay and Heat shape newly triggered voices;
Level is smoothed. No automatic quantization or added hits: polish here means a
consistent synthesized kit and an exact repeating length, not rewritten rhythm.

## Personal Auto mode

Record **one sound per clip**, preferably about 30 hits with at least 200 ms
between attacks. Include quiet, normal and accented versions of the same
articulation. Enroll each sound with its name and MIDI note, then run:

```sh
uv run mouthflow enroll export-pocket --sr 48000
# An enrolled `noise` class becomes rejection, never a sounding voice.
# Additional breath/speech-only clips may be supplied with --reject-clip PATH.
```

Use the Live Set's actual sample rate. Export reads the enrollment manifest;
it does not record audio. It retains all examples up to 30, otherwise selecting
representatives from enrollment alone. At least three complete, isolated
80 ms attacks per playable voice are required. Scale, center and rejection
radius come only from enrollment; query/take audio never fits the profile.
The mislabeled CAL 48k 0003 take and the reserved uptempo take are not enrollment.

Pocket 06.1 fixes large JSON loading: Max's single `readstring` can truncate
at 32,767 characters even while advancing to EOF. The loader reads small
chunks instead. Its scripts and native external use version-specific names
so older Pocket devices can stay loaded alongside this build.

Press **PROFILE**. A successful audio-thread acknowledgement shows the voice
count; malformed files show an error and leave the old profile intact. A rate
mismatch displays the required sample rate and falls back to factory Auto until
a matching profile is loaded. Save the Set after loading. The canonical JSON
is `~/.mouthflow/pocket_profile.json`; export also writes
`uhntiss_pocket_profile.json` into each Max user Library. Keep these personal
profile files local unless you intend to share your voice descriptors.

For named sounds, choose the destination MIDI track and channel under **MIDI TO**.
Put a Drum Rack or other instrument on that track and map pads to the enrolled
notes. **Level 0** silences the built-in kit while MIDI continues. The built-in
synth supplies kick (35/36), snare (38/40) and hat (42/44/46); other notes need
the routed instrument. MIDI is dispatched at the DSP vector boundary through
Max's high-priority clock, independently of the 33 ms display poll. This adds
host scheduling/buffer timing to external instruments; the sample-exact claim
applies to the internal kit and stored event timing, not downstream MIDI.

The three **Teach** buttons remain a quick three-hit soundcheck for the first
three profile voices. Teaching cancels pending capture and replaces that voice's
early examples; it disables the delayed classifier until a complete profile is
exported again. Quick Teach on a fresh device and legacy v4 keep factory rejection. Teaching
a voice in an imported v5 profile keeps its enrolled candidate policy.
The v5 path lets personal examples classify primary candidates even when the
factory forest rejects them; conservative quiet/transient helpers still use
factory gating, so unseen quiet articulations can remain undetected.

V5 live classification uses three nearest 17-feature examples and a learned
support radius. A personal reject bank uses a separate five-neighbour vote,
requiring four negative neighbours; positive examples win ties. When sustained
negative examples exist, the immediate gate uses richer features from the same
first 10 ms. The loop uses five-neighbour classification over 0–10, 10–40 and
40–80 ms slices, with signed Q4 features scaled from enrollment. A following
candidate truncates the window; only complete slices enter the metric. Before
40 ms, the provisional choice stands. Late rejection drops a stored event,
and late relabeling never fires a second audible hit.

State v5 stores each event's MIDI note independently of its current profile;
loading a new mapping cannot retune an existing loop. It reads v2/v3 loops
(resetting obsolete profiles) and exact 156-double v4 profiles. All queues,
voice banks, FFT buffers and loop storage are bounded. Sixteen voices × thirty
examples is the current capacity tradeoff to keep the complete Max state below
32,767 atoms; larger vocabularies need a different state transport.

The 35 ms shared onset separation remains. Overlapping vocal classes, reliable
open-mic speech rejection and speaker-bleed cancellation are unresolved.

## Timing and recovery contract

- Fixed voices sound on the threshold-crossing sample. The same synth voice
  refines its velocity during the next 10 ms without retriggering. Auto waits for
  that analysis window before sounding. Loop playback uses the original onset
  timestamp and the refined velocity, not the later decision time.
- Live's audio-rate `plugphasor~` provides phase. `plugsync~` PPQ anchors the beat
  number. A native unwrapper continues across delayed scheduler messages.
- The loop is eight quarter notes. Tempo changes preserve beat-relative offsets;
  there is no quantization or invented swing. Input/output hardware buffers add
  to monitoring latency. The device does not claim a measured round-trip latency.
- A stop, seek or meter change during capture cancels the partial take and keeps
  the committed loop. An arrangement-loop wrap is treated as a seek: disable it
  or make it longer than the take while recording.
- The up-to-80 ms loop decision that finishes after the seam still belongs to the old
  capture window. It remains part of the same undo transaction.
- A take that would exceed 512 events rolls back instead of silently truncating.
  Closed-hat/snare/kick synthesis has 24 bounded voices with quietest-voice stealing.
- Audio code has no heap allocation, locks, filesystem access, network, Python,
  JavaScript or Live API calls. Message-thread producers serialize before a fixed
  SPSC command queue. UI snapshots cross a separate bounded queue.
- A saturated control queue rejects a command and reports it in the Max console;
  it never blocks the audio thread. An overload is not silently reported as success.

## Build and verify

Core only (C++17, CMake 3.20+, no SDK or Python):

```sh
cmake -S native/pocket -B native/pocket/build -DCMAKE_BUILD_TYPE=Release
cmake --build native/pocket/build -j
ctest --test-dir native/pocket/build --output-on-failure
native/pocket/build/pocket_render pocket-demo.wav
node --test m4l/pocket/build.test.mjs
uv run python -m pytest tests/test_pocket.py
uv run python -m eval.avp_pocket --sr 48000 --enrollment --report avp48.json
uv run python -m eval.pocket_negatives --report negatives.json
uv run python -m eval.pocket_benchmark --report benchmark.json
```

Sanitizers: configure another build directory with `-DPOCKET_SANITIZE=ON`.
On macOS, the direct compiler script also works without CMake:

```sh
git clone https://github.com/Cycling74/max-sdk-base.git ../max-sdk-base
git -C ../max-sdk-base checkout c03a2922a2a8ff149165a1e2ea134e9321d6e202
MAX_SDK_PATH="$PWD/../max-sdk-base" native/pocket/tools/build-mac.sh /tmp/uhntiss-pocket
```

Build uses Apple's command line tools and Node to generate the Max patch/container;
Node is not used when the device runs. Build outside cloud-synced folders to avoid
Finder metadata invalidating ad-hoc code signing. The SDK is build-only and not
vendored or distributed. Release bundles are ad-hoc signed, not notarized.
`build.mjs` also emits the inspectable `.maxpat`. The SDK CMake target can be enabled
with `-DMAX_SDK_PATH=...` on macOS.

For native Auto regression and research (Python is development-only):

```sh
uv run python -m eval.test_native_auto
uv run python -m eval.test_real_auto --build native/pocket/build --report native/pocket/build/real-takes.json
uv run python -m eval.test_transient_auto --binary native/pocket/build/pocket_analyze
uv run --with scikit-learn==1.8.0 python -m eval.heldout_auto --binary native/pocket/build/pocket_analyze --report native/pocket/build/heldout.json
uv run --with scikit-learn==1.8.0 python -m eval.transfer_auto --binary native/pocket/build/pocket_analyze
# Rebuild after regenerating the compiled model, then repeat both regressions.
```

`auto-model.json` records source hashes, exclusions and model configuration.
Training excludes `live_6_uptempo` before reading audio or generating variants.
Every reserved isolated attack and its 200 ms neighbourhood also stays excluded.
Random context augmentation uses only the remaining isolated clips.

`auto-heldout.json` rebuilds a model for each omitted whole take at **48 kHz ×
1 / 0.5 / 0.25 gain**. Each compiled engine identifies its exact model and must
agree with Python on training descriptors and interpolated probes. Capture tests
cover 72 successive two-bar windows with raw velocity and onset error below one
sample. CI selects its frozen Pocket04 baseline by model fingerprints and verifies
it with a paired run that disables only the new path. It fails on unknown models,
per-take regressions, newly missed references and new wrong/extra events, and
requires five recovered quiet hits and three fewer extras. The shipped models match the uptempo-excluded
fold on the development host; every platform independently verifies export parity
and compiled fingerprints. The transient trainer never reads uptempo in any fold.

Results are modest: normal/half-level held-out results remain **243/271** and
**228/271**. On the Mac, quarter-level correct hits improve **220→225** across K/S/H
(Linux retraining: **219→224**); extras
fall **16→13**, with no new wrong hits. The permanent uptempo holdout improves
**25→27/37** at quarter level, but remains **30/37** at normal level: the hard
kick/snare roll misses are unresolved. A frozen set of 27 breath-shaped noise
controls has the same 19 false triggers before and after; it does not prove zero
breath errors. This does not establish stage or unseen-performer reliability.
The recordings informed development; the labels are signal-assisted rather than
independent listening annotations. The 25 ms scoring tolerance is separate from
the sub-sample capture check. Every remaining error is retained in the reports.

The old 271/271 all-data score is no longer the deployment target: keeping the
uptempo source out of training produces 264/271 at the original rate/level and
263/271 at 48 kHz. Full deployed-model regressions at three rates and four gains
are in `auto-real-takes.json`. Read `VALIDATION.md` before treating this as a live
performance instrument.

## Architecture decision

Use **a portable C++17 audio core inside a thin native Max external** for this
milestone. This gives one timing authority inside Live, access to its existing
audio path, and direct deterministic synthesis without a service hop. C++ also
leaves a straightforward route to a JUCE/VST3 wrapper if standalone/plugin
portability becomes a real requirement. Adding that second host now would delay
validating the instrument. No Rust/C++ FFI, Python sidecar or IPC is needed here.

Keep Python for datasets, annotation, offline comparison and model training.
The old `mouthflow` CLI and panels are retained as research/legacy tools; Pocket
never invokes them. Later model improvements must be evaluated against the causal
native baseline and exported as bounded native inference, not put back in the
monitoring path.

Host contracts: [plugphasor~](https://docs.cycling74.com/reference/plugphasor~/),
[plugsync~](https://docs.cycling74.com/reference/plugsync~/),
[pattr](https://docs.cycling74.com/reference/pattr/),
[Max SDK](https://github.com/Cycling74/max-sdk-base).

## Host smoke test

For development, `node m4l/pocket/qa/build-smoke.mjs /tmp/uhntiss-pocket` adds a
separate **QA** device to a built folder. Load that on an empty audio track and
start Live transport. It automatically records six pad hits, clears, undoes,
clears again and restores the `pattr` blob, then writes its result to
`/tmp/uhntiss-pocket-smoke.json`. It is deliberately excluded from the playable
release: it generates test notes and changes its own loop. Stop transport after
the test. This verifies the host adapter; it does not test mic recognition or
physical footswitch hardware.

The additional `qa/build-auto-smoke.mjs` harness feeds reserved real vocal audio
through the new external inside Live, then checks voices, relative timing,
Clear/Undo and v4 state restore. It needs `auto-input.wav` and
`auto-expected.json` in the test build folder, and a host tempo matching the fixture (`beat_seconds`, default 0.5 / 120 BPM).
The whole-take fixture uses 30 BPM so the full 14.4-second recording fits inside
two bars without changing its playback speed. The result is written beside the
fixture as `auto-host-smoke.json`. Its source
player, audio files and QA script are excluded from the playable release.
