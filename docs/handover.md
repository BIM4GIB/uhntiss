# Mouthflow — session handover

Last updated: 2026-06-22. Read this first when picking up work.

## TL;DR
Mouthflow turns a beatbox clip into a drum pattern in Ableton Live. The full
pipeline works end-to-end. A prior session rebuilt the **drum classifier**
(per-user k-NN). **This session fixed onset detection + tempo** — all three eval
metrics are now green. The win was an **octave-corrected tempo estimator** plus
**phase-aware quantization** (snap to a grid aligned to the performance, not
phase-0). **The next frontier is corpus expansion** — the eval is only N=2, so
0.87 F1 is on thin ice; record more mimic takes to harden it.

## Pipeline
`mic/WAV → capture → classify-intent (hardcoded DRUM) → transcribe (onset +
classify → octave-corrected tempo + phase-aware quantize → MIDI) → plan (Claude
picks a kit) → execute (JSON/TCP :9877 → ableton-mcp → Live)`. Only `plan` calls
an LLM; the rest is deterministic Python. CLI process spawns, runs, exits.

## Eval scoreboard (`uv run python -m eval.run_eval`, 2 corpus clips)
| metric | result | target | status |
|---|---|---|---|
| drum-class acc | **0.97** | 0.65 | ✅ |
| onset F1 | **0.87** | 0.75 | ✅ fixed this session (was 0.44) |
| tempo within ±3 | **2/2** | ≥80% | ✅ fixed this session (was 0/2) |

The report now prints a **per-clip** P/R/F1 + detected-vs-GT tempo table.
`uv run python -m eval.onset_sanity` scores the raw detector against the mimic
grids (tempo-independent) — use it as the detector regression guard.

## What fixed onset+tempo this session (so you don't re-derive it)
- **The bug was a 2× octave error.** `librosa.beat.beat_track` reports ~172/207
  BPM on the 84/100 clips. `transcribe._detect_tempo` now takes a base from the
  onset-strength tempogram and **disambiguates the octave** by grid-fit + an
  IOI/band prior, then **`_refine_tempo` sharpens to sub-BPM** (a 0.5 BPM error
  drifts the grid past tolerance late in a clip). Returns `(bpm, confidence)`.
- **Quantization was the other half.** The eval scores *quantized* hit times and
  GT is the *performed* timing, so snapping to a phase-0 grid sheared every hit.
  `_quantise_grid` snaps to a grid **phase-aligned** to the onsets
  (`_grid_phase`), gated on confidence (`_QUANT_CONF_MIN`); low confidence →
  emit raw onset times. Same-slot+pitch collisions dedupe to one hit.
- **Onset detector: leave it mostly alone.** With grid-snapping, onset *timing*
  precision no longer matters — only FP/FN. Tuning experiments (finer hop,
  HF-emphasis envelope, lower delta) all *hurt*; HF-emphasis (`fmin=500`) is
  catastrophic (kills low mouth-kicks). Only kept a mild `delta=0.10` + 40 ms
  `wait` floor (double-trigger suppression). Don't chase detector params on N=2.
- **`--tempo` override** added: `mouthflow record/run/dry-run --tempo 90` skips
  detection; a `"NN bpm"` token in `--hint` does too; M4L has a `tempo` inlet.

## ▶ NEXT WORK: expand the corpus, then the hard timbre cases
1. **Corpus expansion (highest value).** N=2 is too few to trust 0.87. Record
   ~9 mimic takes across 80–120 BPM × boombap/snareheavy/fourfloor (see the
   mimic commands below; ~20 min, headphones). This also feeds the k-NN model.
   *Re-record only after detector changes are stable* — auto-labels depend on
   `_detect_onsets`.
2. **Open vs closed hat** are still collapsed to one `hat` class (needs a
   sustain feature + open-hat data).
3. **Fast-tempo classification** (0.59 held-out @100 BPM) — more exemplars.
- **Measurable loop:** `eval/run_eval.py` (end-to-end) + `eval/onset_sanity.py`
  (detector only) after each change.

## How to run / iterate (this Mac)
```bash
cd ~/UhnTiss/uhntiss && source .env        # plan.py reads ANTHROPIC_API_KEY from env
uv run mouthflow doctor                     # preflight: key, :9877 socket, kit discovery
uv run mouthflow record --duration 8        # mic → Live (Ableton + AbletonMCP must be on)
uv run mouthflow dry-run clip.wav --json    # pipeline only, prints Plan, no Live
uv run python -m eval.run_eval              # score vs tests/fixtures/clips
```
Gather labeled data + retrain the classifier (use **headphones** for mimic takes):
```bash
uv run python -m mimic.take gen   --name X --bpm 90 --preset boombap|snareheavy|fourfloor
uv run python -m mimic.take rec   --name X                 # playrec: plays ref, records mimic (synced)
uv run python -m mimic.take score --name X --clip 03_X     # auto-label + emit corpus clip
uv run python -m eval.train_classifier                     # k-NN from calibration/ + mimic/*.labeled.json
```

## The mimic-a-beat harness (how labeling works)
`mimic/take.py` synthesizes a known reference loop and plays it to your
headphones **while recording your mimic in the same `sounddevice.playrec`
call** — so playback and recording are sample-synced and the reference grid is
*exact ground truth*. `score` finds your reaction-offset, auto-labels each
onset, prints model-vs-heuristic accuracy, dumps `mimic/<name>.labeled.json`
(for retraining), and writes a `tests/fixtures/clips/` corpus trio. No manual
MIDI placement.

## The classifier (what changed)
- `mouthflow/drum_model.json` — per-user **k-NN** model (107 exemplars,
  classes kick/snare/hat). `transcribe._classify` uses it (k-NN exemplar vote
  or nearest-centroid), with `_classify_heuristic` as fallback when absent.
- Trained on **isolated** one-shots (`calibration/*.wav`) + **in-context** hits
  (`mimic/*.labeled.json`). The recipe needed *both* in-context data *and* k-NN:
  nearest-centroid overfit (great at 84 BPM, collapsed at 100 — fast hats darken
  toward snare; a single centroid can't hold a multi-modal class).

## Environment (this Mac)
- Ableton Live 12 Suite. `ableton-mcp` Remote Script at
  `~/Music/Ableton/User Library/Remote Scripts/AbletonMCP`, enabled as a Control
  Surface slot → binds TCP `:9877`. Claude Desktop also has the AbletonMCP MCP
  (`uvx ableton-mcp`, absolute path in `claude_desktop_config.json`).
- `.env` (gitignored) holds `ANTHROPIC_API_KEY`; `source .env` before runs.
- `httpx==0.27.2` pinned (anthropic 0.39 compat).
- Recording uses the MacBook Pro mic via `sounddevice`. Mimic takes need
  headphones (else the reference bleeds into the recording).

## In-Ableton UI (Max for Live device)
- `m4l/Mouthflow.amxd` (+ `mouthflow.js`, README). Installed self-contained copy
  in `~/Music/Ableton/User Library/Devices/` → drag from Live's browser.
- Panel: Generate, duration, count-in, hint, kit dropdown (+ List Kits), status.
  It shells out to `uv run mouthflow record` in `~/UhnTiss/uhntiss`, so it uses
  whatever's on `main` (now the k-NN classifier).
- The `.amxd` is **generated programmatically** (an `ampf` container wrapping
  maxpat JSON) — don't hand-edit; regenerate.

## Known limits / gotchas
- **Fast-tempo classification** is the weak spot (0.59 held-out @100 BPM). More
  mimic takes across tempos → more k-NN exemplars → better.
- **Open vs closed hat** are collapsed to one `hat` class — not separable with
  the current 120 ms features (no sustain cue) and open-hat under-sampled. Needs
  a sustain feature + more open-hat data.
- **`list-kits` pollution:** returns ~430 one-shot samples (`.aif`/`.wav`) mixed
  with 569 real `.adg` racks. Filter to `.adg` in
  `execute.list_drum_instruments`. (See memory `mouthflow-kit-discovery-pollution`.)
- **`:9877` socket** occasionally times out / contends when the Claude Desktop
  MCP and the CLI both connect — just retry. `mouthflow doctor` checks it.

## Workflow conventions
- Branch off `main` → PR → **squash-merge** (history is linear). End commit
  messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Use `git worktree add` for isolated/parallel work.

## Key files
| path | what |
|---|---|
| `mouthflow/transcribe.py` | onsets, features, `_classify` (+heuristic), octave-corrected tempo (`_detect_tempo`/`_refine_tempo`), phase-aware quantize (`_grid_phase`/`_quantise_grid`), MIDI |
| `mouthflow/drum_model.json` | trained k-NN model |
| `mouthflow/cli.py` | `record` / `run` / `dry-run` / `doctor` / `list-kits`; `--tempo` override |
| `mouthflow/plan.py` | Claude planner (`prompts/plan.md`) |
| `mouthflow/execute.py` | ableton-mcp socket client, `list_drum_instruments` |
| `eval/run_eval.py` | end-to-end scoring (now per-clip table) |
| `eval/onset_sanity.py` | tempo-independent onset-detector F1 vs mimic grids |
| `eval/train_classifier.py` | k-NN training |
| `mimic/take.py` | mimic-a-beat labeling harness |
| `calibration/`, `mimic/bb*.*`, `tests/fixtures/clips/` | training + eval data |
| `m4l/` | Max for Live device |
| `docs/spec.md`, `docs/corpus.md`, `docs/mac-handoff.md` | spec + conventions |
