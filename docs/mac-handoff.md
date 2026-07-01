# Mac handoff — first run on macOS

The project was scaffolded on Windows. Ableton Live and the microphone
work live on the Mac. This doc walks you from a fresh clone to a
successful `mouthflow run` end-to-end.

## 0. Prerequisites

- macOS (any recent version that runs Ableton Live 11+)
- Ableton Live 11 or 12
- Python 3.11 (the project is pinned to `>=3.11,<3.13`)
- An Anthropic API key
- A microphone (built-in is fine for testing)

## 1. Clone and install

```bash
git clone https://github.com/BIM4GIB/uhntiss.git
cd uhntiss

# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Creates .venv/ and installs pinned deps from uv.lock
uv sync
```

Sanity check — the full suite must pass (all offline, no Live or mic needed):

```bash
uv run pytest
```

## 2. Configure git identity for this clone

Global git config is probably your day-job identity; commits from this
repo should be attributed to your personal account:

```bash
git config user.email rene@pellicer.dk
git config user.name "Rene"
```

`gh` CLI auth is orthogonal — any account with push access to
`BIM4GIB/uhntiss` is fine.

## 3. Install ableton-mcp as a Live Remote Script

Mouthflow talks to Live via [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)'s
TCP socket on port 9877. The Python side is **not** the interesting
bit — we don't use the MCP host. What matters is the **Remote Script**
that runs inside Live.

```bash
# Clone somewhere outside this repo
git clone https://github.com/ahujasid/ableton-mcp.git ~/code/ableton-mcp
```

Follow that repo's README for the Remote Script install — typically:

1. Copy `AbletonMCP_Remote_Script/` to
   `~/Music/Ableton/User Library/Remote Scripts/AbletonMCP/` (create the
   folder if it doesn't exist).
2. In Live → Preferences → Link, Tempo & MIDI, set one of the Control
   Surface slots to **AbletonMCP**.
3. Restart Live. The log (`~/Library/Preferences/Ableton/Live <ver>/Log.txt`)
   should show AbletonMCP loading and listening on 9877.

Verify from another shell:

```bash
python -c "import socket; s=socket.socket(); s.connect(('127.0.0.1',9877)); print('ok'); s.close()"
```

### 3b. (Optional but recommended) Splice in the bridge fork

Two features need Remote Script commands that stock ableton-mcp lacks:
`transcribe-clip` (transcribe the clip selected in Live — the main clip-based
workflow) needs `get_selected_clip`, and drone macro automation needs
`set_clip_envelope`. Splice both into the installed
`~/Music/Ableton/User Library/Remote Scripts/AbletonMCP/__init__.py` following
[`bridge/README.md`](../bridge/README.md) (back up first), then **fully restart
Live** — toggling the Control Surface reuses the cached module and will NOT
pick up the edit. Without the fork everything else still works; the two
features fail gracefully with clear messages.

## 4. Environment

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Put it in your shell profile or a `.env` file you source — just don't
commit it (`.gitignore` already excludes `.env`).

## 5. Ground truth: fixtures, mimic takes, and the per-user classifier

The tracked corpus is currently **two fixture trios** —
`tests/fixtures/clips/01_boombap_mimic.*` and `02_bb100.*` (`.wav` + intended
`.mid` + `.json` metadata; convention in [`corpus.md`](corpus.md)). The
historical spec's 20-clip plan was superseded: drum quality in practice comes
from the **per-user k-NN classifier**, trained from mimic takes recorded
against reference grids (see [`../mimic/README.md`](../mimic/README.md)) via
`uv run python -m eval.train_classifier` → `mouthflow/drum_model.json`.
Pitched (bass/lead) ground truth uses `mimic/<name>.notegrid.json` scored by
`eval/note_eval.py` — recording real tonal takes is a pending next step.
Expanding both corpora is welcome; N is small and that is a known limitation.

## 6. Smoke test: dry-run

Verify the pipeline without touching Live:

```bash
uv run mouthflow dry-run tests/fixtures/clips/01_boombap_mimic.wav \
    --instruments "query:Drums#Kit-Core%20808,query:Drums#Kit-Core%20Jazz" \
    --hint "loose and sloppy"
```

You should see progress on stderr and the `Plan` JSON on stdout. If
this fails, fix it before touching Live — debugging the socket layer
on top of a broken pipeline is a trap.

## 7. End-to-end: run against Live

1. Open Ableton, confirm AbletonMCP is loaded (see §3).
2. Open an empty set (File → New Live Set).
3. Preflight with `mouthflow doctor` — it checks that `ANTHROPIC_API_KEY`
   is set, that AbletonMCP answers on :9877, and that the browser returns
   loadable kits, exiting non-zero if anything's missing:

```bash
uv run mouthflow doctor
```

4. Run:

```bash
uv run mouthflow run tests/fixtures/clips/01_boombap_mimic.wav \
    --instruments "query:Drums#Kit-Core%20808"
```

Expect: a new MIDI track appears, a drum rack loads, a clip plays the
transcribed pattern, tempo updates to match.

Gotchas and how to diagnose:

| Symptom | Likely cause |
|---|---|
| Not sure which prereq is broken | Run `mouthflow doctor` — it isolates key vs socket vs kit-discovery |
| `ConnectionRefusedError` on :9877 | Remote Script not loaded or Live not running |
| `AbletonError: Unknown command ...` | ableton-mcp version mismatch; update both sides |
| Track created but empty | `instrument_path` URI doesn't exist; check `--instruments` values |
| MIDI notes audible but nothing plays | Drum rack didn't load; same as above |

The manual test protocol also lives at
[`tests/README.md`](../tests/README.md).

## 8. Record your own

```bash
uv run mouthflow record --duration 10 --device drums   # or bass | lead | drone | auto
uv run mouthflow record-stream --device bass            # open-ended; type 'stop' to finish
uv run mouthflow transcribe-clip --device bass          # selected Live clip (needs §3b fork)
```

Records (or reads the selected clip), runs the full pipeline. Pitched voices
take `--bars auto|4|8|16`, `--correct/--no-correct`, `--key`, `--scale`. The
success gate: *"tweak this or start from scratch?"* answer must be **tweak**.

## 9. Run the eval

```bash
uv run python -m eval.run_eval
```

This scores transcription on the tracked fixtures: onset F1, drum-class
accuracy, tempo error (see [`../eval/README.md`](../eval/README.md) for all
eval tools + current numbers). Drum thresholds live in
`mouthflow/devices/drum/` (`mouthflow/transcribe.py` is only a back-compat
facade); the planner prompt is `mouthflow/prompts/plan.md`.

For taste review (A/B vs Ableton's native "Convert Drums to MIDI"):

```bash
# Lists which baseline MIDI files still need exporting from Ableton
uv run python -m eval.baseline_ableton

# Once <stem>.mouthflow.wav and <stem>.baseline.wav are rendered for
# every clip, rate them:
uv run python -m eval.taste_review
```

## 10. Known friction and limits

- **Instrument selection is session-aware.** When Live is reachable the
  CLI walks ableton-mcp's browser via
  `AbletonClient.list_drum_instruments()` (recursing the Drums category,
  keeping `is_loadable` leaves) and hands the planner real
  `query:Drums#FileId_NNNNN` URIs as `{name, uri}` pairs — so `run` /
  `record` load a real kit with no `--instruments` flag. `--instruments`
  still overrides if you want a specific set. The synthetic
  `_FALLBACK_INSTRUMENTS` in [`cli.py`](../mouthflow/cli.py) is only used
  for the offline `dry-run` path; those URIs do **not** load in a real
  install.
- **Drum classifier** is a per-user k-NN (`mouthflow/drum_model.json`)
  trained on mimic takes, with the hand-tuned heuristic as fallback when no
  model exists. Retrain after recording new takes:
  `uv run python -m eval.train_classifier`.
- **No realtime.** Offline only, per spec non-goals.
- **Windows vs Mac.** The project runs on both, but all the Ableton /
  mic work happens on Mac. If you make changes on Windows, run the
  full pipeline on Mac before calling it done — synthetic tests don't
  cover socket or audio-device quirks.

## 11. Where to look next

- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — current architecture + per-voice maturity
- [`docs/handover.md`](handover.md) — living status / next work
- [`docs/KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md) — honest gap list
- [`docs/spec.md`](spec.md) — *historical* v0 vision spec (thesis + rationale)
- [`docs/corpus.md`](corpus.md) — labelling convention
- [`mouthflow/prompts/plan.md`](../mouthflow/prompts/plan.md) — the
  Claude planner prompt. Replace the placeholder few-shots with real
  examples from your corpus as soon as it exists.
