# UHNTISS Pocket · drums milestone 01

A local voice-to-drum instrument and two-bar looper for Ableton Live. Native C++
handles detection, synthesis, transport and recording. Max for Live supplies the
host connection and controls. Python remains an offline research tool.

**Performance alpha, not a stage-certified release.** Fixed-voice layering is the
predictable path. Automatic kick/snare/hat recognition is experimental, even after
teaching. This milestone does not claim professional performer endorsement.

## Play

Requires macOS 11+, Ableton Live with Max for Live, and a mono mic input. The
provided external is universal Apple Silicon/Intel. The portable core also builds
on Linux; there is no Windows Max external in this release.

1. Keep `UHNTISS Pocket.amxd`, `pocket-ui.js` and `uhntiss.pocket01~.mxo` together.
   Drag the `.amxd` onto an **audio track**. No API key, bridge, Python, Node server,
   network connection or downloaded kit is needed to play.
2. Select your mono mic channel in Live; set the track's Monitor to **In**. The
   device produces drum sound; it does not pass the dry mic through. Use your
   normal low-latency interface setup and headphones while using an open mic.
3. Start with **Input Voice → Kick**. Vocal attacks trigger the kick immediately.
   Adjust Input and Gate until quiet room noise stops triggering and soft hits
   still register. Use the kick/snare/hat pads to test the kit without a mic.
4. Run Live in **4/4**. Press **Record 2 Bars** before a downbeat. The display moves
   from armed to recording at the next bar; play for two bars. The new loop starts
   at the following seam. If armed with transport stopped, start Live and watch
   the armed/recording display for the actual capture boundary.
5. Select **Overdub**, switch Input Voice to Snare or Hat, and record another pass.
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

Choose Auto, press **Teach K**, and make three isolated kick sounds, then repeat
with **Teach S** and **Teach H**. Leave a short gap between sounds. Each teaching
button cancels an in-progress take and teaches the next three detected attacks;
it leaves the committed loop intact. Record or Undo exits teaching. Teaching
changes the profile, not the loop undo history. All three voices must have three
examples before the profile is used. The profile and loop are stored with the
Live device/set via a `pattr` blob.

This uses four causal features from the first 8 ms after detection. Some vocal
kick/snare attacks are indistinguishable in that window. Teaching does not solve
that fundamental limit. Use a fixed voice when the response needs to be certain.
Open hats, simultaneous vocal sounds, continuous rolls and bleed suppression are
outside this milestone. The onset detector has a 24 ms refractory period.

## Timing and recovery contract

- Detection stores the threshold-crossing beat; classification finishes 8 ms
  later. Loop playback uses the stored timestamp and velocity, not decision time.
- Live's audio-rate `plugphasor~` provides phase. `plugsync~` PPQ anchors the beat
  number. A native unwrapper continues across delayed scheduler messages.
- The loop is eight quarter notes. Tempo changes preserve beat-relative offsets;
  there is no quantization or invented swing. Input/output hardware buffers add
  to monitoring latency. The device does not claim a measured round-trip latency.
- A stop, seek or meter change during capture cancels the partial take and keeps
  the committed loop. An arrangement-loop wrap is treated as a seek: disable it
  or make it longer than the take while recording.
- Classification that finishes just after the seam still belongs to the old
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
```

Sanitizers: configure another build directory with `-DPOCKET_SANITIZE=ON`.
On macOS, the direct compiler script also works without CMake:

```sh
git clone https://github.com/Cycling74/max-sdk-base.git ../max-sdk-base
git -C ../max-sdk-base checkout cc03a2922a2a8ff149165a1e2ea134e9321d6e202
MAX_SDK_PATH="$PWD/../max-sdk-base" native/pocket/tools/build-mac.sh /tmp/uhntiss-pocket
```

Build uses Apple's command line tools and Node to generate the Max patch/container;
Node is not used when the device runs. Build outside cloud-synced folders to avoid
Finder metadata invalidating ad-hoc code signing. The SDK is build-only and not
vendored or distributed. Release bundles are ad-hoc signed, not notarized.
`build.mjs` also emits the inspectable `.maxpat`. The SDK CMake target can be enabled
with `-DMAX_SDK_PATH=...` on macOS.

For the independent native detector report (Python research environment only):

```sh
uv run python -m eval.native_pocket
```

The existing Python transcription oracle is not evidence about this native
classifier. The default native Auto report gives onset F1 about 0.33/0.47 and
matched class accuracy 0.73/0.44 on the two existing fixtures. Those references are
auto-labelled grid MIDI, not hand-labelled vocal onset truth, and are too small
for a performance claim. They nevertheless make the recognition weakness clear.
The next recognition milestone needs a diverse, hand-labelled, held-out corpus
and performer audition of false triggers, missed ghost notes and latency.

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
