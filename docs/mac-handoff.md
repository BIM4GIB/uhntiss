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

Sanity check — should be 21 passed:

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

## 4. Environment

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Put it in your shell profile or a `.env` file you source — just don't
commit it (`.gitignore` already excludes `.env`).

## 5. Build the corpus (spec step 4)

This is the blocking dependency for the eval ship gate. Spec wants 20
clips across tempos 70–160 BPM and styles boom-bap / trap / dnb /
breakbeat, both clean and sloppy.

For each clip produce the trio:

```
tests/fixtures/clips/01_basic_4to4.wav
tests/fixtures/clips/01_basic_4to4.mid
tests/fixtures/clips/01_basic_4to4.json
```

- `.wav` — beatbox it, any DAW. Mouthflow normalises to 44.1 kHz /
  16-bit / mono, so format doesn't matter at input.
- `.mid` — hand-place the drums you **meant** in Ableton against the
  audio, export as MIDI. This is your taste made explicit.
- `.json` — `{"tempo": 92, "style": "boom-bap", "notes": "..."}`. See
  [`corpus.md`](corpus.md) for the full convention.

Two or three evenings, no shortcuts — this is the ground truth the
transcriber gets tuned against.

## 6. Smoke test: dry-run

With one clip in `tests/fixtures/clips/`, verify the pipeline without
touching Live:

```bash
uv run mouthflow dry-run tests/fixtures/clips/01_basic_4to4.wav \
    --instruments "query:Drums#Kit-Core%20808,query:Drums#Kit-Core%20Jazz" \
    --hint "loose and sloppy"
```

You should see progress on stderr and the `Plan` JSON on stdout. If
this fails, fix it before touching Live — debugging the socket layer
on top of a broken pipeline is a trap.

## 7. End-to-end: run against Live

1. Open Ableton, confirm AbletonMCP is loaded (see §3).
2. Open an empty set (File → New Live Set).
3. Run:

```bash
uv run mouthflow run tests/fixtures/clips/01_basic_4to4.wav \
    --instruments "query:Drums#Kit-Core%20808"
```

Expect: a new MIDI track appears, a drum rack loads, a clip plays the
transcribed pattern, tempo updates to match.

Gotchas and how to diagnose:

| Symptom | Likely cause |
|---|---|
| `ConnectionRefusedError` on :9877 | Remote Script not loaded or Live not running |
| `AbletonError: Unknown command ...` | ableton-mcp version mismatch; update both sides |
| Track created but empty | `instrument_path` URI doesn't exist; check `--instruments` values |
| MIDI notes audible but nothing plays | Drum rack didn't load; same as above |

The manual test protocol also lives at
[`tests/README.md`](../tests/README.md).

## 8. Record your own

```bash
uv run mouthflow record --duration 10 \
    --instruments "query:Drums#Kit-Core%20808"
```

Counts in silently, records for `--duration` seconds, runs the full
pipeline. The spec success gate: *"tweak this or start from scratch?"*
answer must be **tweak**.

## 9. Run the eval

Once all 20 corpus clips are in place:

```bash
uv run python -m eval.run_eval
```

This scores transcription: onset F1, drum-class accuracy, tempo error.
Ship gate targets are in the printed report. Iterate on
`mouthflow/transcribe.py` thresholds and `mouthflow/prompts/plan.md`
until the gate hits.

For taste review (A/B vs Ableton's native "Convert Drums to MIDI"):

```bash
# Lists which baseline MIDI files still need exporting from Ableton
uv run python -m eval.baseline_ableton

# Once <stem>.mouthflow.wav and <stem>.baseline.wav are rendered for
# every clip, rate them:
uv run python -m eval.taste_review
```

## 10. Known friction and limits

- **`--instruments` is manual.** ableton-mcp exposes a browser tree;
  v0.1 doesn't traverse it and hands you a fallback list. If you want
  real session-aware instrument choice, add a `get_session_info` call
  that recurses `get_browser_items_at_path` for the Drums category and
  passes real URIs into `available_instruments`. Noted in
  [`cli.py`](../mouthflow/cli.py) near `_FALLBACK_INSTRUMENTS`.
- **Heuristic classifier** is hand-tuned, not trained. Expect
  misclassifications on breath-heavy hats and tonal snares. Fixing
  this is v0.2 (real classifier trained on the corpus).
- **No realtime.** Offline only, per spec non-goals.
- **Windows vs Mac.** The project runs on both, but all the Ableton /
  mic work happens on Mac. If you make changes on Windows, run the
  full pipeline on Mac before calling it done — synthetic tests don't
  cover socket or audio-device quirks.

## 11. Where to look next

- [`docs/spec.md`](spec.md) — full v0 spec, roadmap, architecture
- [`docs/corpus.md`](corpus.md) — labelling convention
- [`mouthflow/prompts/plan.md`](../mouthflow/prompts/plan.md) — the
  Claude planner prompt. Replace the placeholder few-shots with real
  examples from your corpus as soon as it exists.
