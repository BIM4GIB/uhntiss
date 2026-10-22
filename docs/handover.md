# Mouthflow — session handover

Last updated: 2026-10-22 (state as of the last commit, PR #18, 2026-07-04).
Read this first when picking up work.
(Architecture overview for newcomers: [`ARCHITECTURE.md`](ARCHITECTURE.md);
honest gap list: [`KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md); audit findings
+ the forward plan: [`roadmap.md`](roadmap.md); voice-dataset design:
[`corpus.md`](corpus.md).)

**2026-07-03/04 — PRs #13–#18: bass fixed on real takes, then a dataset
pipeline so the next fixes are measured, not guessed.** Verified 2026-10-22:
136 tests pass, eval gates green (onset F1 0.87 / class acc 0.95 / tempo 2/2
/ timing MAE 38 ms), installed panels identical to the repo at stamp
`5c2c663`. Know about:

- **Planner strict tool use (#13).** The first real `claude-sonnet-5` call
  returned the `clips` tool argument as a *stringified* JSON object and the
  take died in pydantic after the LLM call. `emit_plan` is now `strict: true`
  (wire schema post-processed: `additionalProperties: false`, value
  constraints stripped — `_LLMPlan` still enforces them) plus a
  belt-and-braces un-stringify of JSON-in-JSON tool fields. Also:
  `tests/conftest.py` isolates `~/.mouthflow` (kit cache + take vault) from
  every test — a CLI test had been writing fake `Kit 0..9` URIs into the real
  kit cache.
- **Bass on real vaulted takes (#14, #15).** Diagnosed by replaying
  `~/.mouthflow/takes/` (the vault paying off; `transcribe-clip` now
  bookkeeps to `last_take.json` too). The clip played adjacent-semitone
  *clusters* — pitch-wobble fragments quantising into one grid slot, plus
  velocity-20 first notes and a forced snap onto a project grid the hum never
  sat on. Fixes, all in `devices/pitched.py` / `refine.py` / `signal.py`:
  `_enforce_monophony` for `MONOPHONIC` clips (same-slot notes collapse to
  the most confident, overlaps clamp legato); `fit_to_bars auto` tolerates
  0.10 bar of boundary overshoot (4.05 bars → 4 bars, not 8 with 4 silent);
  **pitched grid trust** — note starts snap only when `signal.grid_fit ≤
  0.20`, and then onto the performer-phase grid, otherwise performed timing
  stays raw; velocity floors (pitched anchors 60/90/115, hard floor 40 —
  quiet hummed notes are weak phonation, not ghost intent); bass
  `min_note_s` 0.08 → 0.05 and re-articulation splitting on a 23 ms RMS so
  staccato 8ths survive. The 11:27 take went 17 smeared notes → 25 clean
  monophonic notes, min velocity 52. **Conclusion: pyin is now the
  bottleneck** (octave errors), which is what the dataset is for.
- **Version-stamped devices (#15).** Every panel title and glue ready-line
  carries the build SHA (`MOUTHFLOW · BASS · 5c2c663`, `*` if dirty) so "am I
  running latest?" is answerable inside Live. `generate.py --install`
  **removes superseded files** (bare `Mouthflow.amxd` + `mouthflow.js` →
  `MouthflowDrums.amxd`, which got the voice-neutral controls in #14).
- **Voice dataset harness (#16, #17, #18).** Ground truth from *the
  performer's own voice*, no manual annotation: every row is a reference
  riff the performer imitates, so the reference grid **is** the label.
  `mimic/tonal.py` (`gen`/`rec`/`score`; bass riffs `roots`/`pump8`/`funk`,
  lead `steps`/`arp`/`held`, drone `hold`/`chord` — each stresses one failure
  mode), `mimic/session.py` (guided resumable runner; plans `starter_bass`
  20 = 12 calibrate / 8 eval, `starter_lead` 10, `starter_drone` 5; eval
  rows use unseen keys/tempos and are recorded last, never tuned against),
  and `mimic/live_ingest.py` (**Live-native flow**: `setup` drops the riffs
  into the open set as MIDI clips on `MF Ref <plan>` tracks, you record
  audio takes next to them, `ingest` fetches the *selected* take clip via
  the bridge, rebuilds the grid at the current project tempo, scores, gates
  and ingests). The pitched panels have a **DATASET column**
  (`next / ingest / keep / skip / setup`; the headphone-playrec `record`
  path remains reachable through the glue). Accepted rows land in
  `datasets/<voice>/` + `manifest.jsonl` (gitignored — voice stays local).
  **Progress: `starter_bass` 2/20** (b01, b02: both `roots`, F1 0.55 each,
  b02 with 4 octave errors out of 10 notes). Both came through the
  playrec session flow at sha `8e78451` (panel or terminal — not logged); the Live-native `setup`/`ingest`
  round-trip is offline-tested only (not yet run in a real set).
- **Gate to the next quality work:** when `starter_bass` reaches 20/20,
  calibrate bass thresholds to this voice on the calibrate rows and run the
  pyin-vs-SwiftF0 A/B on the eval split *with a number*. Then roadmap item 8
  (installer + retake-in-place) and planner enrichment (item 9).

**2026-07-03 (later) — the "feel batch" (`feat/next-honest-feel-speed`) landed
the roadmap's NEXT items 5–7.** Know about:

- **The eval can fail now.** `uv run python -m eval.run_eval` exits non-zero
  below its gates (onset F1 ≥0.75, class acc ≥0.65, tempo ≥80%, timing MAE
  ≤45ms) and CI runs it. It prints an honesty warning on the default corpus
  (fixtures are train-contaminated; held-out ≈0.73 via `classifier_cv`).
  Swing-preservation + velocity rank-correlation are reported (n/a until real
  labelled takes exist). `note_eval` counts wrong-pitch matches against
  precision; `mimic/take.py` refuses to emit corpus clips from misaligned
  takes (`--force` overrides).
- **Groove:** drums quantise to a **swing-aware grid** — classic off-8th
  shuffle and off-16th swing measured separately, each gated by floors
  calibrated against real articulation drag plus a consistency test; a
  shuffled performance keeps its shuffle, straight takes are unchanged.
  `bar_align` translates the performer grid onto the downbeat instead of
  shearing per-hit. Tempo octave errors fixed in both directions: sub-76-BPM
  doubling (70 detects correctly) and sparse-take halving (kick/snare modal
  IOI anchors the octave — hats mark subdivisions and are ignored for this).
  Velocities are normalised **per take**
  (ghosts ~45, median 90, accents ~120, scaled by the take's dynamic spread) —
  mic gain no longer decides dynamics.
- **Speed:** kit lists cache to `~/.mouthflow/kits-<category>.json` (24h TTL)
  and are refreshed on a second connection *while the mic records*; the
  planner prompt caches the instrument list (system-block breakpoints), so
  repeat takes re-process only the take summary. Planner model is now
  `claude-sonnet-5` (thinking disabled for the forced tool call — note that
  model rejects non-default temperature). `--device auto` routes on a 6s
  window. Stage timings are logged (`heard … (1.2s)`, `plan: … (3.1s)`).

**2026-07-03 — the "trust batch" (`feat/now-trust-batch`) landed the roadmap's
NOW items.** Behaviour changes to know about:

- **One clock:** `record` now fetches the project tempo before capture (like
  `record-stream`/`transcribe-clip`) and `apply_plan` **no longer sets the
  Live set's tempo by default** — pass `--set-tempo` to restore the old
  behaviour. Clips are inserted in beats, so they follow the project tempo.
  For a solo take NOT performed against the set's grid (e.g. a fresh empty
  project), `--detect-tempo` re-enables detection (add `--set-tempo` to also
  push it to the set — the old behaviour, now explicit).
- **Never lose a take:** the Ableton socket is opened/pinged *before* the mic;
  takes persist to `~/.mouthflow/takes/` with flags in
  `~/.mouthflow/last_take.json`; `mouthflow retry-last` replays the last take.
  A silent take exits (code 3) before the LLM call instead of landing an empty
  clip. Socket errors close the connection (no more desync) and read-only
  commands retry once.
- **Feedback:** `record --countin N` runs the count-in CLI-side (the M4L glue
  no longer fires "go" ~0.5s before the mic opens); `record-stream` emits
  `level <dBFS>` lines — the glue routes them to a new `level` outlet and the
  pitched panels show them in a number box next to the record buttons;
  `doctor` reads `.env` for the API key and `doctor --bridge` probes the fork
  commands (transport failures are classified as "couldn't ask", never as an
  answer — see `AbletonTransportError` in `execute.py`).
- **Correctness:** drone notes carry pyin confidence and are never
  scale-snapped (a single-pitch drone used to be silently transposed via the
  C-major fallback); drone bar-fit extends held notes to the loop end;
  overlapping same-pitch notes survive `_midi_to_notes` (FIFO); phase-aware
  quantise clamps at 0 (negative times crashed mido); `fit_to_bars` allows
  **1/2-bar loops** and trims whole empty lead-in bars.

## TL;DR
Mouthflow is now an **umbrella product**: a shared "voice → MIDI → Ableton"
engine with a registry of per-voice **devices**. Four voices ship today —
**drums** (the original, unchanged behaviour), **bass**, **lead**, and
**drone/ambient**. The pipeline verbs are the same as before; what varies per
voice is captured in a `DeviceSpec`. The drum path is **byte-identical** to
pre-refactor (guarded by `eval/run_eval.py`).

## Pipeline
`mic/WAV → capture → classify (router) → transcribe (per-device) → refine
(pitched-only: note correction + bar-fit) → plan (Claude picks an instrument)
→ execute (JSON/TCP :9877 → ableton-mcp → Live)`. Only `plan` calls an LLM.
CLI spawns, runs, exits.

**`mouthflow/refine.py`** (new stage, pitched voices only; drums pass through):
- **Note correction** — auto-detects the key (Krumhansl-Schmuckler,
  confidence-weighted) or takes `--key`/`--scale`, then scale-snaps **only
  notes pyin was unsure about** (`confidence < keep_confident=0.75`).
  Confident notes are trusted as sung — basslines are chromatic, and forcing
  one scale corrupted clearly-hummed notes (found on a real take: a
  0.99-confidence E2 was being "corrected" to D#2). `--no-correct` bypasses.
- **Fit-to-bars** — `--bars auto|off|4|8|16`: sizes the clip to a whole bar
  count (auto rounds *up* so nothing is cut), clamps note overhang, and forces
  the plan's `length_bars` so the clip loops cleanly on the project grid.
- Pitched notes below `min_confidence=0.2` are dropped as breath/attack blips
  (`devices/pitched.py`), and `NoteEvent.confidence` carries pyin's per-note
  voiced-probability through the pipeline.

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
  real durations. Bass vs lead = a `VoiceConfig` row only. Segmentation uses
  **hysteresis** (`min_stable_s`, default 80 ms): a pitch change must hold to
  start a new note, so natural vibrato/glides don't shatter one note into many.
  Known limit: pyin's analysis window (bass 4096 ≈ 93 ms) blurs notes faster
  than ~1/8 in the bass register — inherent low-freq trade-off, not a bug.
- **Drone:** stable-pitch regions → one dominant region = held note, several =
  a chord (notes enter in sequence, all sustain to the bar-snapped clip end and
  ring together). Clip loops in Live → continuous drone; movement comes from the
  pad preset. Plus a loudness **contour → device-macro automation** envelope.

## ▶ NEXT WORK
1. **Browser categories — DONE (confirmed against Live 12.3).** The voices use
   the Sounds tree: bass=`sounds/Bass` (515 presets), lead=`sounds/Synth Lead`
   (342), drone=`sounds/Ambient & Evolving` (hundreds of pad/drone presets).
   `list-kits --device <voice>` returns real loadable
   `query:Sounds#<folder>:FileId_NNNNN` URIs. (A `doctor` per-category probe is
   still a nice-to-have but not blocking.)
2. **Bridge fork — INSTALLED on the dev Mac (2026-06-29), half-verified.**
   Both commands are spliced into the installed Remote Script:
   `get_selected_clip` is **verified live** (drives `transcribe-clip` and the
   in-device Transcribe Clip button, round-trip confirmed on a real clip);
   `set_clip_envelope` compiles + passed adversarial review but its **LOM
   behaviour is runtime-unverified** — first real drone-automation run should
   follow the checklist in `bridge/README.md`. On a stock ableton-mcp install
   both features are absent: `transcribe-clip` fails with a clear error, drone
   degrades to a held note/chord (`apply_plan` logs "automation skipped").
   Remote Script edits need a **full Live restart** (Control-Surface toggle
   reuses the cached module).
3. **Pitched eval + tonal dataset — HARNESS DONE (PRs #16–#18), DATA
   PENDING.** `eval/note_eval.py` scores a take against a reference grid
   (note P/R/F1 + octave-error, onset-aligned, wrong-pitch matches count
   against precision, source-dependent `offset_range`). The reference clips
   now live in Live (`mimic/live_ingest.py setup`) or play over headphones
   (`mimic/session.py`). What's missing is **the performer at the mic**:
   `starter_bass` is 2/20, lead and drone 0. No bass/lead threshold tuning
   or pyin replacement until the eval split exists — anything else is
   fitting noise.
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
uv run mouthflow record-stream --device bass            # open-ended take; 'stop' on stdin ends it
uv run mouthflow transcribe-clip --device bass          # selected Live clip → Live (needs bridge fork)
uv run mouthflow dry-run clip.wav --device drone --json # pipeline only, prints Plan
uv run mouthflow retry-last                 # replay the last vaulted take (~/.mouthflow/takes/)
uv run python -m eval.run_eval              # drum oracle — gated, exits non-zero on regression
uv run pytest -q                            # full suite — must all pass (136)
python m4l/generate.py --install            # regenerate stamped panels + sync, removes superseded files

# voice dataset (see docs/corpus.md)
uv run python -m mimic.session --list                       # plans + progress
uv run python -m mimic.session run --plan starter_bass --output <headphones idx>   # guided playrec session
uv run python -m mimic.live_ingest setup  --plan starter_bass   # reference MIDI clips into the open set
uv run python -m mimic.live_ingest ingest --plan starter_bass   # score + ingest the SELECTED take clip
cat datasets/manifest.jsonl                                  # per-row scores, role, pipeline sha
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
- `m4l/Mouthflow.amxd` is the bare **template**; the shipped devices are the
  generated `Mouthflow{Drums,Bass,Lead,Drone}.amxd`. Each panel loads a
  per-voice glue copy (`mouthflow_<voice>.js`) with the voice **baked into
  the JS default** (not loadbang — that races Node-for-Max startup). The glue
  subprocess-spawns `uv run mouthflow …`, so Python changes reach the device
  with **no regen**.
- `m4l/generate.py` **injects the in-device controls**: all panels get
  Transcribe Clip, record_start/record_stop (open-ended takes via
  `record-stream`) and the input level meter; pitched panels add bars /
  correct (default ON) / key / scale fields and the **DATASET column**
  (`data_next`, `data_ingest`, `data_keep`, `data_skip`, `data_setup`, each
  panel with its own plan baked in). Panel titles + the glue ready-line carry
  the **build stamp** (`git rev-parse --short HEAD`, `*` when dirty) — check
  it against `git log` before debugging a "wrong" result.
  `--install` syncs panels + glue + `package.json` into
  `~/Music/Ableton/User Library/Devices` (with `.bak` backups) and **removes
  superseded files** (`Mouthflow.amxd`, `mouthflow.js`) — required, because
  Live loads from there and `node.script` resolves the glue **next to the
  .amxd**. Always version-stamp + install after a panel or glue change.
- **Layout gotcha (verified on-screen):** Live's device strip is fixed-height
  (~196 px) and silently clips anything below — new controls must go in a
  **column to the right**, never stacked underneath. A fresh browser drag loads
  the current file; no Live restart needed for panel changes.

## Proven vs unverified (honest surface for reviewers)
| capability | offline tests | verified in Live | notes |
|---|---|---|---|
| drum transcribe (classify/tempo/quantize) | ✅ (regression oracle) | ✅ many sessions | most mature voice |
| bass transcribe + refine (correct/bar-fit) | ✅ | ✅ real takes (2026-06/07) | confidence-gating validated on a real take |
| bass monophony + grid trust + velocity floors (#14/#15) | ✅ | ✅ replayed vaulted takes | 17 smeared → 25 clean notes; octave errors remain (pyin) |
| planner strict tool use on `claude-sonnet-5` (#13) | ✅ (mocked) | ✅ live dry-run end-to-end | plan 3.6 s on a real call |
| tonal dataset: playrec session flow (#16/#17) | ✅ self-test F1 1.0 | ✅ 2 bass rows ingested | starter_bass 2/20 |
| tonal dataset: Live-native `setup`/`ingest` (#18) | ✅ (plan chunking, row notes) | ❌ not yet run in a real set | needs bridge `get_selected_clip` |
| lead transcribe | ✅ synthetic only | ⚠️ not yet on real voice | config clone of bass |
| drone held note/chord | ✅ | ⚠️ lightly | — |
| drone loudness→macro automation | client+serialization only | ❌ runtime-unverified | needs `set_clip_envelope` LOM check |
| `transcribe-clip` (bridge fork) | client tested | ✅ round-trip verified | needs the fork spliced |
| `record-stream` start/stop | ✅ (mocked stream) | ⚠️ via device buttons, lightly | — |
| M4L panels (drums/bass/lead/drone, stamped) | container+JS validated | ✅ render + controls verified on-screen | DATASET buttons: whether the 2 rows came via panel or terminal isn't logged |
| `--device auto` router | heuristic unit-tested | ❌ no labelled routing set | can misroute hummed chords |

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
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Every change keeps `uv run pytest` (136) and `uv run python -m eval.run_eval`
  green (the eval is gated and CI-enforced; its class-acc number is a train-set
  upper bound — the honest held-out figure is `classifier_cv`'s ≈0.73).
- Panel or glue change → `python m4l/generate.py --install` → commit the
  stamped `.amxd`/`.js` in a follow-up `chore(m4l): panels stamped + installed`.
- Debug a "the clip sounds wrong" complaint by replaying the vaulted take
  (`mouthflow retry-last`, or `dry-run ~/.mouthflow/takes/<ts>.wav --json`),
  never by guessing from the description.
- Dataset rows tagged `eval` are **never** tuned against; changes to
  pitched thresholds must not regress `datasets/manifest.jsonl` eval scores.

## Key files
| path | what |
|---|---|
| `mouthflow/signal.py` | shared DSP (onset/tempo/features/quantise/write_midi, `grid_fit`/`grid_phase` trust helpers) |
| `mouthflow/devices/base.py`, `registry.py` | DeviceSpec + registry |
| `mouthflow/devices/drum/` | drum transcriber + k-NN classifier + `drum_model.json` |
| `mouthflow/devices/pitched.py` | bass + lead transcriber (pyin) |
| `mouthflow/devices/{bass,lead}/` | device specs + prompts |
| `mouthflow/devices/drone/` | drone transcriber + contour + device + prompt |
| `mouthflow/refine.py` | pitched post-processing: note correction (confidence-gated scale snap) + fit-to-bars/loop |
| `mouthflow/classify.py` | intent router (`--device auto`) |
| `mouthflow/plan.py` | Claude planner (per-device prompt + summary; strict `emit_plan` tool on `claude-sonnet-5`) |
| `mouthflow/execute.py` | ableton-mcp socket client, `list_instruments`, `set_clip_envelope` |
| `bridge/` | forked Remote Script command source + install docs (drone automation) |
| `m4l/` | Max for Live panels + `generate.py` (stamping, install, supersede) + glue |
| `mimic/tonal.py`, `session.py`, `live_ingest.py` | voice dataset harness: riff gen/score/ingest, guided plans, Live-native setup/ingest |
| `mimic/take.py` | drum mimic harness (label sanity gate) |
| `datasets/` (gitignored) + `datasets/manifest.jsonl` | recorded voice rows with role + scores + pipeline sha |
| `tests/conftest.py` | isolates `~/.mouthflow` (kit cache, take vault) from all tests |
| `docs/corpus.md` | dataset design: roles, starter plans, growth loop |
| `docs/spec.md` | original v0 spec (drums-era; architecture has since generalized) |
