# Architecture

Orientation for a cold reviewer; per-voice maturity is stated honestly at the
bottom.

## What it is

Mouthflow turns a vocal performance into MIDI in a running Ableton Live set.
You beatbox, hum, sing, or drone into a mic (or point it at an audio clip
already in Live); a deterministic DSP pipeline transcribes it; a single Claude
call picks a fitting instrument from the set's actual browser; the result is
created as a playing clip over ableton-mcp's TCP socket. One shared engine,
four "voices" (drums, bass, lead, drone) expressed as entries in a typed
device registry.

## Pipeline

```
capture -> classify -> transcribe -> refine -> plan -> execute
```

| Stage | What it does | File |
|---|---|---|
| capture | Mic record / WAV normalise to 44.1 kHz 16-bit mono | [`mouthflow/capture.py`](../mouthflow/capture.py) |
| classify | Intent router for `--device auto` (voiced fraction + pitch stability); skipped when the voice is explicit | [`mouthflow/classify.py`](../mouthflow/classify.py) |
| transcribe | Per-device WAV -> notes (`Transcription`), via the device's `Transcriber` | [`mouthflow/devices/`](../mouthflow/devices/) |
| refine | Pitched-only post-processing: scale-snap + bar-fit. **Drums pass through untouched** | [`mouthflow/refine.py`](../mouthflow/refine.py) |
| plan | **The only LLM call in the codebase.** One Claude request (tool-use, strict schema) picks an instrument URI + track name | [`mouthflow/plan.py`](../mouthflow/plan.py) |
| execute | Apply the `Plan` over the ableton-mcp socket: track, instrument, clip, notes, tempo, fire | [`mouthflow/execute.py`](../mouthflow/execute.py) |

Everything except `plan.py` is deterministic DSP/plumbing — no model calls,
no network beyond the local Live socket. The wiring lives in `_run_pipeline`
in [`mouthflow/cli.py`](../mouthflow/cli.py).

## Device registry

Each voice is a frozen `DeviceSpec` ([`devices/base.py`](../mouthflow/devices/base.py)),
registered by import ([`devices/registry.py`](../mouthflow/devices/registry.py)).
The fields that matter:

- `transcriber` — the one real behavioural difference (WAV -> `Transcription`).
- `clip_mode` — `PERCUSSIVE` (16th quantise, GM ch 9) / `MONOPHONIC` (real
  durations, ch 0) / `SUSTAINED` (held notes); also decides whether `refine`
  applies (pitched modes only).
- `browser_category` — where in Live's browser to discover loadable presets
  (`Drums`, `sounds/Bass`, `sounds/Synth Lead`, `sounds/Ambient & Evolving`;
  confirmed against Live 12.3).
- `plan_summary` + `prompt_path` — how the transcription is described to the
  planner, plus the per-voice system prompt.

| Voice | Transcriber | Notes |
|---|---|---|
| drums | [`devices/drum/`](../mouthflow/devices/drum/) | Beatbox -> GM drums. Per-user k-NN timbre classifier over the 10-feature vector in [`features.py`](../mouthflow/devices/drum/features.py) (model in `drum_model.json`, heuristic fallback when absent). Octave-correct tempo + phase-aware, confidence-gated quantisation in [`tempo.py`](../mouthflow/devices/drum/tempo.py) |
| bass | [`devices/pitched.py`](../mouthflow/devices/pitched.py) | `librosa.pyin` -> continuous f0 -> segmentation with hysteresis (`min_stable_s=0.08` — a pitch change must hold before it starts a new note, so vibrato/glides don't shatter one note) -> octave-snap to E1–E3 |
| lead | same `PitchedTranscriber` | Bass and lead differ **only** by a `VoiceConfig` row (range, target octave G3–C6, finer grid) — no algorithm fork |
| drone | [`devices/drone/`](../mouthflow/devices/drone/) | Stable pitch regions -> one held note, or a hummed sequence -> a ringing chord; loudness contour -> an `AutomationEnvelope` ([`contour.py`](../mouthflow/devices/drone/contour.py)) |

## Shared DSP

[`mouthflow/signal.py`](../mouthflow/signal.py) holds the voice-neutral
primitives (onset/tempo detection, feature extraction, `quantise`,
`write_midi`, `velocity_from_rms`). [`mouthflow/transcribe.py`](../mouthflow/transcribe.py)
is a **back-compat facade** only: it re-exports the historic names so `eval/`,
`mimic/` and the tests keep importing them — no drum logic lives there any more.

## Confidence gates — the quality philosophy

Prefer dropping or leaving a note alone over guessing. Three gates:

1. **Blip drop** ([`pitched.py`](../mouthflow/devices/pitched.py)): a segment
   whose mean pyin voiced-probability is below `min_confidence=0.2` is a
   breath/attack artefact — dropped, not emitted.
2. **Trust confident notes** (`refine.correct_notes`): scale-snap applies
   ONLY to notes with confidence < `keep_confident=0.75`. Basslines are
   chromatic in practice; forcing every note into one scale corrupted
   clearly-pitched notes on a real take, so confident notes are left as
   performed and only wobbly ones are nudged (≤1 semitone).
3. **Tempo gate** ([`drum/tempo.py`](../mouthflow/devices/drum/tempo.py)):
   grid quantisation runs only when tempo confidence ≥ `_QUANT_CONF_MIN=0.5`;
   below that, raw onset times are emitted rather than snapping to a grid the
   estimator doesn't trust.

`refine.py` also auto-detects the key (Krumhansl-Schmuckler correlation over a
duration-and-confidence-weighted pitch-class histogram; C major fallback),
overridable via `--key`/`--scale`; `fit_to_bars` sizes the clip so it loops:
`auto` rounds the take UP to 4/8/16 (multiples of 8 past that), assumes 4/4,
clamps note overhang, and unrecognised values fall back to auto rather than
raising mid-pipeline.

## Ableton integration

Two layers, one socket (TCP `:9877`, JSON per message):

- **Stock [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)
  Remote Script** — everything `apply_plan` needs: `create_midi_track`,
  `load_browser_item`, `create_clip`, `add_notes_to_clip`, `set_tempo`,
  `fire_clip`, plus browser traversal for instrument discovery.
- **Forked bridge** ([`bridge/`](../bridge/README.md)) — two extra commands
  that must be **manually spliced** into the installed Remote Script:
  `get_selected_clip` (powers `transcribe-clip`; without it that command exits
  with a clear "is the forked bridge installed?" error) and `set_clip_envelope`
  (powers drone contour automation; without it `apply_plan` logs
  "automation skipped" and the drone still plays as a held note/chord).

On the dev machine the fork is installed and `get_selected_clip` is verified
live; `set_clip_envelope` compiles and is reviewed but its LOM behaviour has
never been exercised in Live (see the runtime checklist in the bridge README).

## Max for Live layer

[`m4l/generate.py`](../m4l/generate.py) generates the per-voice `.amxd` panels
from the proven drums template: it rewrites only the `ptch` chunk (container
round-trip is byte-identical), injects the in-device controls (transcribe-clip,
record start/stop, bars, correct toggle default-ON, key, scale), and bakes the
voice into a per-voice glue copy `mouthflow_<voice>.js` — NOT via loadbang,
which races Node-for-Max startup. Layout gotcha: Live's device strip is
fixed-height (~196 px) and clips vertical overflow, so the new controls form a
**second column to the right** and the device grows wider, not taller.
`--install` syncs panels + glue + `package.json` into
`~/Music/Ableton/User Library/Devices` with `.bak` backups (node.script
resolves the glue next to the `.amxd`, so both must live there). The glue
spawns `uv run mouthflow ...` as a subprocess, so the device always runs the
current Python code — no panel regeneration needed for Python changes.

## CLI

Commands ([`mouthflow/cli.py`](../mouthflow/cli.py)): `record`, `record-stream`
(open-ended mic capture, stops on `stop` via stdin), `run`, `dry-run`,
`doctor`, `input-devices`, `transcribe-clip`, `list-kits`. Key flags:
`--device drums|bass|lead|drone|auto`, `--tempo`, `--bar-align`,
`--correct/--no-correct`, `--key`, `--scale`, `--bars auto|off|4|8|16`,
`--instruments`, `--input`, `--json`.

## Module map

| Concept | Where |
|---|---|
| Pipeline wiring + CLI | [`mouthflow/cli.py`](../mouthflow/cli.py) |
| Shared DSP | [`mouthflow/signal.py`](../mouthflow/signal.py) |
| Back-compat facade | [`mouthflow/transcribe.py`](../mouthflow/transcribe.py) |
| Device abstraction + registry | [`mouthflow/devices/base.py`](../mouthflow/devices/base.py), [`registry.py`](../mouthflow/devices/registry.py) |
| Per-voice devices | [`mouthflow/devices/{drum,bass,lead,drone}/`](../mouthflow/devices/), [`pitched.py`](../mouthflow/devices/pitched.py) |
| Pitched post-processing | [`mouthflow/refine.py`](../mouthflow/refine.py) |
| Planner (the only LLM call) | [`mouthflow/plan.py`](../mouthflow/plan.py) + [`prompts/`](../mouthflow/prompts/) |
| Live socket client + apply | [`mouthflow/execute.py`](../mouthflow/execute.py) |
| Forked Remote Script commands | [`bridge/`](../bridge/README.md) |
| M4L panels + generator + glue | [`m4l/`](../m4l/) |
| Quality measurement | [`eval/`](../eval/) (`run_eval` drum oracle, `classifier_cv` honest CV, `note_eval` pitched P/R/F1) |
| Labelled-take harness + reference grids | [`mimic/`](../mimic/) |

## Per-voice maturity

Legend: **live** = verified in a running Live set; **offline** = covered by
tests/eval on recorded or synthetic data; **unverified** = code exists,
reviewed, never exercised at runtime.

| Voice | Transcribe | Plan | Execute | Automation |
|---|---|---|---|---|
| drums | **live** — most mature; regression oracle (`eval/run_eval.py`), honest classifier number: LOO ≈ 0.81, held-out-take mean ≈ 0.73 (`eval/classifier_cv.py`) | live | live | n/a |
| bass | **live** — verified on real hummed takes in Live | live | live | n/a |
| lead | **offline** — shares the bass code path, tests pass, but untested on a real sung take | offline | offline (same path as bass) | n/a |
| drone | **offline** — held note/chord works in tests; synthetic only | offline | offline | **unverified** — needs the bridge fork; `set_clip_envelope` never run in Live |

Test suite: `uv run pytest` -> 75 passed. The tracked eval corpus is small —
two fixture trios (`01_boombap_mimic`, `02_bb100`) under `tests/fixtures/clips/`
plus the labelled takes in `mimic/`; pitched eval (`eval/note_eval.py`) has
only a synthetic self-test, and its lone fixture is a bass-range melody: bass
scores F1 1.0 on clean sines, while the lead row octave-snaps that same melody
into G3–C6 and scores 0 — lead has no meaningful eval number yet. Treat the
numbers as measured-but-low-N.
