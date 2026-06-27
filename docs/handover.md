# Mouthflow — session handover

Last updated: 2026-06-26. Read this first when picking up work.

## TL;DR
Mouthflow is now an **umbrella product**: a shared "voice → MIDI → Ableton"
engine with a registry of per-voice **devices**. Four voices ship today —
**drums** (the original, unchanged behaviour), **bass**, **lead**, and
**drone/ambient**. The pipeline verbs are the same as before; what varies per
voice is captured in a `DeviceSpec`. The drum path is **byte-identical** to
pre-refactor (guarded by `eval/run_eval.py`).

## Pipeline (unchanged shape)
`mic/WAV → capture → classify (router) → transcribe (per-device) → plan (Claude
picks an instrument) → execute (JSON/TCP :9877 → ableton-mcp → Live)`. Only
`plan` calls an LLM. CLI spawns, runs, exits.

Pick the voice with `--device drums|bass|lead|drone` (or `--device auto` to let
the router classify by ear). Default is `drums`.

## Architecture: shared core + devices registry
- `mouthflow/signal.py` — **shared DSP** (tempo/onset detection, feature
  extraction, `quantise`, `write_midi(channel=…, duration_s-aware)`,
  `velocity_from_rms`). Voice-neutral.
- `mouthflow/devices/base.py` — `DeviceSpec` (id, intent, transcriber,
  `ClipMode`, browser_category, instrument_filter, prompt_path, plan_summary,
  fallback) + the `Transcriber` protocol.
- `mouthflow/devices/registry.py` — `register` / `get_device(intent)` /
  `get_device_by_id(id)`. Devices self-register on import of
  `mouthflow/devices/__init__.py`.
- `mouthflow/transcribe.py` — now a **back-compat facade**: `transcribe_drums`
  delegates to the drum device, and the historic names (`_SR`,
  `_detect_onsets`, `_features_at`, `_classify*`, `_quantise_16th`,
  `_write_midi`) are re-exported so `eval/`, `mimic/`, and tests keep importing
  them. **Don't reintroduce drum logic here** — it lives in `devices/drum/`.
- `mouthflow/plan.py` — `make_plan(device=…)` selects the per-voice prompt +
  `plan_summary`. A drums `DeviceSpec` yields a byte-identical request to
  `device=None`. `_system_prompt` is path-keyed (multi-device safe).

## The four voices
| device | intent | transcriber | clip mode | what it does |
|---|---|---|---|---|
| drums | DRUM | `devices/drum` (k-NN GM classifier) | percussive | unchanged from before |
| bass | BASS | `devices/pitched` (pyin) | monophonic | hum → low monophonic MIDI, octave-snapped to E1–E3 |
| lead | MELODY | `devices/pitched` (pyin) | monophonic | config clone of bass, G3–C6, finer grid, legato |
| drone | DRONE | `devices/drone` (pyin regions) | sustained | held note / hummed-chord + loudness→macro automation |

- **Pitched (bass/lead):** `librosa.pyin` → continuous f0 → segment by held
  semitone-change + gap (NOT onsets — they fire spuriously on sustained tones),
  merge same-pitch fragments, octave-snap. `signal.write_midi(channel=0)` with
  real durations. Bass vs lead = a `VoiceConfig` row only.
- **Drone:** stable-pitch regions → one dominant region = held note, several =
  a chord (notes enter in sequence, all sustain to the bar-snapped clip end and
  ring together). Clip loops in Live → continuous drone; movement comes from the
  pad preset. Plus a loudness **contour → device-macro automation** envelope.

## ▶ NEXT WORK
1. **Confirm Live browser categories at runtime (biggest unknown).** bass/lead/
   drone default to `browser_category="Instruments"` with synthetic fallback
   URIs. Run `get_browser_tree("instruments")` / `("sounds")` against the real
   Live 12 set, capture the exact sub-folder paths for bass / lead-synth /
   pad-ambient presets, confirm loadable leaves return real
   `query:…#FileId_NNNNN` URIs, and bake them into each device's
   `browser_category`/`instrument_filter`/fallback. Add per-category probes to
   `doctor`.
2. **Install + verify the drone automation bridge.** The contour→automation
   needs the forked Remote Script command `set_clip_envelope` — see `bridge/`
   (source + install + LOM runtime-verification checklist). Without it, drone
   still plays as a held note/chord (`apply_plan` logs "automation skipped").
3. **Pitched eval + tonal mimic (needs recorded data).** Generalize
   `eval/run_eval.py` with note-level P/R/F1 + octave-error via `mir_eval`
   (dev dep), and extend `mimic/take.py` with a tonal reference + pitch scorer
   so labeled pitched ground truth can be gathered. Infrastructure can be built
   now; scoring needs the user to record tonal mimic takes.
4. **Drums onset/tempo** — PR #6's octave-correct tempo + phase-aware
   quantization is **merged and integrated into the drum device**
   (`devices/drum/tempo.py`, `devices/drum/transcriber.py`); confidence-gated
   so it only quantises when the tempo is trusted. Re-check the eval on the
   current corpus and keep expanding it (N is small).

## How to run / iterate (this Mac)
```bash
cd ~/UhnTiss/uhntiss && source .env        # plan.py reads ANTHROPIC_API_KEY
uv run mouthflow doctor                     # preflight: key, :9877 socket, kit discovery
uv run mouthflow record --device bass --duration 8     # mic → Live (Ableton + AbletonMCP on)
uv run mouthflow dry-run clip.wav --device drone --json # pipeline only, prints Plan
uv run python -m eval.run_eval              # drum oracle (class acc must stay ~0.97)
uv run pytest -q                            # 55 tests
python m4l/generate.py                       # regenerate bass/lead/drone M4L panels
```

## The drum classifier / mimic harness (unchanged, now under devices/drum)
- `devices/drum/classify.py` — per-user **k-NN** model (`drum_model.json`),
  loaded **lazily at first classify** (not import). To force the heuristic in a
  test, patch `_MODEL` on *that* module.
- `mimic/take.py` — the mimic-a-beat labeling harness (synthesize reference,
  sample-synced `playrec`, auto-label). Still drum-specific; the tonal variant
  is item 3 above.
- `eval/run_eval.py` + `eval/train_classifier.py` — drum scoring + k-NN
  training, still the regression oracle for the drum path.

## In-Ableton UI (Max for Live)
- `m4l/Mouthflow.amxd` (proven drums panel) + generated
  `Mouthflow{Bass,Lead,Drone}.amxd`. All share `m4l/mouthflow.js`, which
  forwards `--device <id>`. `m4l/generate.py` regenerates the per-voice panels
  (container round-trip is verified; **smoke-test generated panels in Live**).

## Known limits / gotchas
- **Browser-category strings + fallback URIs** for the new voices are
  placeholders until item 1 is done; offline fallbacks don't resolve in real
  Live (true for drums too).
- **Drone automation** needs the bridge fork installed (item 2).
- **Router (`--device auto`)** is heuristic; a hummed *chord* drone routes to a
  pitched voice — use `--device drone` explicitly for that.
- **`:9877` socket** occasionally contends when Claude Desktop's MCP and the CLI
  both connect — retry; `mouthflow doctor` checks it.

## Workflow conventions
- Branch off `main` → PR → **squash-merge**. End commit messages with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Every change keeps `uv run pytest` and `uv run python -m eval.run_eval` green
  (drum class acc ~0.97 is the oracle).

## Key files
| path | what |
|---|---|
| `mouthflow/signal.py` | shared DSP (onset/tempo/features/quantise/write_midi) |
| `mouthflow/devices/base.py`, `registry.py` | DeviceSpec + registry |
| `mouthflow/devices/drum/` | drum transcriber + k-NN classifier + `drum_model.json` |
| `mouthflow/devices/pitched.py` | bass + lead transcriber (pyin) |
| `mouthflow/devices/{bass,lead}/` | device specs + prompts |
| `mouthflow/devices/drone/` | drone transcriber + contour + device + prompt |
| `mouthflow/classify.py` | intent router (`--device auto`) |
| `mouthflow/plan.py` | Claude planner (per-device prompt + summary) |
| `mouthflow/execute.py` | ableton-mcp socket client, `list_instruments`, `set_clip_envelope` |
| `bridge/` | forked Remote Script command source + install docs (drone automation) |
| `m4l/` | Max for Live panels + `generate.py` + glue |
| `docs/spec.md` | original v0 spec (drums-era; architecture has since generalized) |
