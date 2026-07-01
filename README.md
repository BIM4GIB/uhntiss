# Mouthflow

A voice-driven arrangement agent for Ableton Live. Make a sound with your
voice, get it into your session — as **drums**, **bass**, **lead**, or an
**ambient drone**.

Status: **working 4-voice umbrella** — a shared voice→MIDI→Ableton engine with
a device registry. Drums are the most mature voice (regression oracle + per-user
classifier); bass has been verified on real takes in Live; lead and drone work
but are less exercised. Pitched output gets confidence-gated note correction and
bar-fit/looping. Known weaknesses are catalogued honestly — reviewers should
start there.

**Reading order for reviewers:**
1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — current architecture + per-voice maturity
2. [`docs/KNOWN-LIMITATIONS.md`](docs/KNOWN-LIMITATIONS.md) — ranked, honest gap list
3. [`docs/handover.md`](docs/handover.md) — living status / next work
4. [`eval/README.md`](eval/README.md) — quality methodology + actual numbers

See also [`docs/spec.md`](docs/spec.md) *(historical v0 vision spec — predates
most of the code)*, [`docs/corpus.md`](docs/corpus.md) (labelling convention),
and [`docs/mac-handoff.md`](docs/mac-handoff.md) (first-run setup on the Mac).

> Repo codename: `uhntiss`. Project name `mouthflow` is a placeholder —
> rename before the first public release if something better shows up.

## Quickstart

Prereqs: Python 3.11, [uv](https://docs.astral.sh/uv/), Ableton Live 11+,
[ableton-mcp](https://github.com/ahujasid/ableton-mcp) installed as a Live
Remote Script, and an `ANTHROPIC_API_KEY` in your environment.

> **Forked-bridge features:** `transcribe-clip` (transcribe the clip selected
> in Live) and drone macro automation need two extra Remote Script commands
> that are **not** in stock ableton-mcp — splice them in per
> [`bridge/README.md`](bridge/README.md). Everything else runs on a stock
> install.

```bash
uv sync
uv run pytest                             # 75 tests, all offline
uv run mouthflow doctor                   # preflight: API key, Live socket, browser
uv run mouthflow record --device bass     # 15s capture → pipeline → applied to Live
uv run mouthflow record-stream --device lead   # open-ended take; 'stop' on stdin ends it
uv run mouthflow transcribe-clip --device bass # transcribe the clip selected in Live (needs fork)
uv run mouthflow run clip.wav --device drone   # skip the capture step
uv run mouthflow dry-run clip.wav --device lead --json  # pipeline, print Plan, don't touch Live
```

`--device` is one of `drums | bass | lead | drone` (default `drums`), or
`auto` to route by ear. Pitched voices also take `--bars auto|off|4|8|16`
(fit + loop), `--correct/--no-correct`, `--key`, `--scale` (note correction).
Other commands: `input-devices`, `list-kits`. See
[`docs/handover.md`](docs/handover.md) for the current state of each voice.

The Max-for-Live device panels live in [`m4l/`](m4l/README.md) —
`python m4l/generate.py --install` regenerates them and syncs them into Live's
User Library.

## Layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module map and data
flow. Quality methodology and current numbers: [`eval/README.md`](eval/README.md).

## License

MIT. See [`LICENSE`](LICENSE) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
