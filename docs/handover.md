# Mouthflow — session handover

Last updated: 2026-06-22. Read this first when picking up work.

## TL;DR
Mouthflow turns a beatbox clip into a drum pattern in Ableton Live. The full
pipeline works end-to-end. This session rebuilt the **drum classifier** as a
per-user k-NN model (the big win) and added an in-Ableton **Max for Live**
device. **The next frontier is onset detection + tempo** — now the dominant
bottleneck per the eval.

## Pipeline
`mic/WAV → capture → classify-intent (hardcoded DRUM) → transcribe (onset +
classify → MIDI + tempo) → plan (Claude picks a kit) → execute (JSON/TCP
:9877 → ableton-mcp → Live)`. Only `plan` calls an LLM; the rest is
deterministic Python. CLI process spawns, runs, exits (no server).

## Eval scoreboard (`uv run python -m eval.run_eval`, 2 corpus clips)
| metric | result | target | status |
|---|---|---|---|
| drum-class acc | **0.97** | 0.65 | ✅ fixed this session (was 0.62) |
| onset F1 | 0.44 | 0.75 | ❌ **next** |
| tempo within ±3 | 0/2 | ≥80% | ❌ **next** |

## ▶ NEXT WORK: onset detection + tempo (the remaining "detecting" problem)
The classifier is solid; detecting is what's still weak.
- **Onsets** — `transcribe._detect_onsets` (librosa `onset_detect`) misses fast
  hi-hat rolls and mis-segments. Onset F1 0.44.
- **Tempo** — `transcribe._detect_tempo` (librosa `beat_track`) is unreliable on
  beatbox. Wrong tempo also corrupts `transcribe._quantise_16th`, which shifts
  every hit off-grid → drags onset F1 down too.
- **How to iterate (measurable loop):** gather labeled clips with the mimic
  harness → `eval/run_eval.py` after each change.
- **Ideas:** tune `onset_detect` (delta, pre/post-max, backtrack) or a
  percussive-specific onset; derive tempo from inter-onset intervals or accept a
  `--tempo`/`--hint`; consider skipping quantization until tempo is trustworthy.

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
| `mouthflow/transcribe.py` | onsets, features, `_classify` (+heuristic), tempo, quantize, MIDI |
| `mouthflow/drum_model.json` | trained k-NN model |
| `mouthflow/cli.py` | `record` / `run` / `dry-run` / `doctor` / `list-kits` |
| `mouthflow/plan.py` | Claude planner (`prompts/plan.md`) |
| `mouthflow/execute.py` | ableton-mcp socket client, `list_drum_instruments` |
| `eval/run_eval.py`, `eval/train_classifier.py` | scoring + training |
| `mimic/take.py` | mimic-a-beat labeling harness |
| `calibration/`, `mimic/bb*.*`, `tests/fixtures/clips/` | training + eval data |
| `m4l/` | Max for Live device |
| `docs/spec.md`, `docs/corpus.md`, `docs/mac-handoff.md` | spec + conventions |
