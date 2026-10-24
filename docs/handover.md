# Mouthflow — session handover

Last updated: 2026-10-24 (branch `feat/pocket-avp-enroll` = PR #19 + main + `feat/enroll` + the Pocket wiring).
Read this first when picking up work.
(Architecture overview for newcomers: [`ARCHITECTURE.md`](ARCHITECTURE.md);
honest gap list: [`KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md); audit findings
+ the forward plan: [`roadmap.md`](roadmap.md); voice-dataset design:
[`corpus.md`](corpus.md).)

**Catch-up checklist (added 2026-10-24 after a miss):** `git fetch` FIRST,
then `git branch -r`, `gh pr list --state open --draft`, and read every open
PR's description before summarising state. Merged history + main cannot see
in-flight work; on 2026-10-22 a catch-up reported "last work = PR #18" while
PR #19 (Pocket 05, the native rebuild, 10 commits, CI green) sat open as a
draft.

**2026-10-24 — Pocket wired to enrollment + AVP (`feat/pocket-avp-enroll`,
PR into `feat/native-pocket-drums`).** Contract doc:
[`pocket-integration.md`](pocket-integration.md). `mouthflow/pocket.py`
replicates Pocket's Teach metric; `mouthflow enroll export-pocket` turns the
enrolled clips into a 3-medoid-per-voice profile file; the external gained a
`profile <156>` message (`decodeProfile` in `state.hpp`, +4 native checks,
compiles universal with the SDK) and the device a **PROFILE** button
(`pocket-profile.js`, not yet clicked in Live). `eval/avp_pocket.py` numbers
(25 performers): onset recall 92.6 %; factory forests **47.7 %** on other
voices (nearly everything → hat); Teach 71.7 %; 30-example profile 72.5 %;
`all123` k-NN on the same onsets **81.1 %**. The gap is the feature
vector/metric, not profile size — that's the cross-team question for Codex.
Built device bundle for a first click: `m4l/pocket/build/` (gitignored).

**2026-10-21 — UHNTISS Pocket 05 (PR #19, DRAFT, open, by Codex): the native
realtime rebuild.** The set on this Mac runs it. Lives in
`native/pocket/` (+ `m4l/pocket/`, `eval/*_auto.py`) on
`feat/native-pocket-drums`; 0 conflicts with main as of 2026-10-24. Know:

- **What it is:** a C++ Max external (`uhntiss.pocket05~.mxo`) — realtime
  beatbox → synthesized kick/snare/hat + 2-bar looper with overdub/replace/
  undo, 10 ms classification deadline, no Python/bridge/API key at play time.
  Its README: "Python remains an offline research tool."
- **Its own classifier:** three random forests over 17 features in a 10 ms
  window (primary 300 trees, recovery, 48 kHz transient tracker), trained
  with scikit-learn on René's isolated hits + 7 full takes, `live_6_uptempo`
  held out (30/37 at normal level). **Teach K/S/H** = 3 examples per class,
  3-NN, stored in the device's `pattr` blob. Open hats map to closed hat.
- **Relation to mouthflow (this doc's main subject):** two stacks, not
  connected. mouthflow = offline clip → Claude kit choice → quantised MIDI
  clip (MouthflowDrums panel spawns the CLI); Pocket = realtime instrument.
  Roadmap item 12 framed exactly this split (realtime loop + offline
  "polish truth"). Models can't be shared (17 features/10 ms vs
  123 features/200 ms); **data and evals can**: score Pocket's forests on
  AVP (`eval/avp_eval.py`) — every Pocket number is one performer — and
  feed `mouthflow enroll` clips (one class per clip, ~30 hits) into Teach
  (3/class is below the 5/class that already loses 3 pts on AVP).
- Codex's working copy + calibration project:
  `~/Documents/Codex/2026-10-21/i-w-2/{work/uhntiss, outputs/}`. The 48 kHz
  calibration take there (`CAL 48k … 0003`) has its snare and hat sections
  swapped; Codex has not been told "recorded".

**2026-10-24 — enrollment (`feat/enroll`): user calibration is a product
flow now.** `mouthflow enroll` + the drums panel's ENROLL column:

- **One class per clip.** Record a ~12 s clip of only kicks, only snares,
  only hats (open hat optional) — in Live (select the clip, press its class
  button; needs the `get_selected_clip` bridge like Transcribe Clip), from a
  file (`enroll file kick x.wav`), or from the mic (`enroll record kick`).
  The clip is the label; nothing to annotate, nothing to swap. Any
  rate/channels are accepted (resampled to 44.1 k mono into
  `~/.mouthflow/enroll/` + `manifest.json`).
- **`enroll train`** extracts `all123`, cross-validates (stratified folds
  over hits — optimistic, same-clip) and writes `~/.mouthflow/drum_model.json`
  atomically. `classify._load_model` now prefers that file over the
  packaged `mouthflow/drum_model.json` (your dev enrollment from
  `eval/train_classifier`). `enroll status` / `enroll reset --yes`.
- **Why these numbers:** AVP says ~30 hits/class → 83 % vs 80 % at 5/class,
  and a separate open-hat class costs nothing on kick/snare/hat (83.4 %
  either way; open vs closed separate at 72–87 %). `TARGET_HITS=30`,
  `MIN_HITS=12` (warns "thin").
- Tests isolate `ENROLL_DIR` + `_USER_MODEL_PATH` (conftest). 162 tests.
  Not yet done in Live: pressing the ENROLL buttons on a real clip (the
  CLI path was smoke-tested on the calibration one-shots under a fake HOME).

**2026-10-22 — cross-performer drums (`feat/avp-cross-performer`).** The
drum classifier finally has numbers from more than one voice, and it changed
because of them. Know about:

- **AVP dataset in the lab.** `eval/avp.py` loads the Amateur Vocal
  Percussion dataset (25 amateurs on a MacBook mic, 4,314 hand-annotated
  hits, CC BY 4.0; gitignored under `datasets/avp/`, fetch instructions in
  the module). `eval/avp_eval.py` reproduces the three protocols of the
  MIT-licensed reference project (beatboxing-to-midi) plus 25-way
  leave-one-performer-out and full-enrollment calibration, over every
  featurizer × classifier. BLAS threads are capped inside it — four uncapped
  runs in parallel produced nothing in 71 minutes.
- **Production classifier changed** (`devices/drum/features.py`,
  `classify.py`): feature set `all123` (= the old `drum10` + 13 MFCC
  mean/std/delta + a level-invariant 3-slice log-mel shape) selected by the
  model file's `feature_set` key (legacy files default to `drum10`). Three
  tiers: per-user k-NN `drum_model.json` → AVP-trained MLP
  `drum_global.json` (new; what a fresh install gets) → heuristic. Your model
  was retrained on `all123`: held-out 0.734 → 0.736, LOO 0.808 → 0.800
  (noise at 260 hits); drum oracle 0.98 class acc, all gates green, 151 tests.
  Extraction is 3.3 ms/onset.
- **The numbers** (annotated onsets; reference project in brackets): unseen
  voices 77.4 [60.3], improvisations 79.6 [71.9], calibrated 5/class 80.0
  [74.5], full enrollment 83.4, leave-one-performer-out 68.0. The heuristic
  fallback scored 41.7 on other voices (kick 0.9 %). Snare is the hard class
  everywhere (40–60 %). Per-performer feature normalisation adds ~1 pt to
  the global model (not shipped: needs running per-user stats); the
  roadmap's embedding+prototype recipe lost to plain k-NN; a per-take
  loudness column was neutral. Detected-onset protocol tracks these within
  2 pts (onset recall 96.2 %).
- **Next: user calibration = enrollment — DONE 2026-10-24** (see the block
  above). Still open: enrolling a voice that isn't René's is the first real
  test of the global-fallback → enrollment path.

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
(2026-10: there are TWO device lines now — the native realtime **Pocket** on PR #19, and the Python/M4L **mouthflow** panels below. See the Pocket block above.)

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
uv run mouthflow enroll clip kick           # enroll the SELECTED Live clip as kicks (then snare, hat[, hat_open])
uv run mouthflow enroll train               # -> ~/.mouthflow/drum_model.json (your voice beats the global model)
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
| `mouthflow/devices/drum/` | drum transcriber + 3-tier classifier (`features.py` feature sets, `classify.py`), `drum_model.json` (packaged dev enrollment) + `drum_global.json` (AVP MLP); `~/.mouthflow/drum_model.json` wins when present |
| `mouthflow/enroll.py` | per-user enrollment: one-class clips → hits → `~/.mouthflow/drum_model.json` (`mouthflow enroll …`) |
| `eval/avp.py`, `eval/avp_eval.py`, `eval/train_global.py` | AVP loader, cross-performer eval matrix, global-model trainer |
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
