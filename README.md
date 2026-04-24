# Mouthflow

A voice-driven arrangement agent for Ableton Live. Beatbox a rhythm, get a
drum pattern in your session. That's it.

Status: **v0 — pre-corpus.** See [`docs/spec.md`](docs/spec.md) for the full
spec, [`docs/corpus.md`](docs/corpus.md) for the labelling convention,
and [`docs/mac-handoff.md`](docs/mac-handoff.md) for first-run setup on
the Mac (Ableton + microphone live there).

> Repo codename: `uhntiss`. Project name `mouthflow` is a placeholder —
> rename before the first public release if something better shows up.

## Quickstart

Prereqs: Python 3.11, [uv](https://docs.astral.sh/uv/), Ableton Live 11+,
[ableton-mcp](https://github.com/ahujasid/ableton-mcp) installed as a Live
Remote Script, and an `ANTHROPIC_API_KEY` in your environment.

```bash
uv sync
uv run mouthflow record        # 15s capture → pipeline → applied to Live
uv run mouthflow run clip.wav  # skip the capture step
uv run mouthflow dry-run clip.wav --json  # pipeline, print Plan, don't touch Live
```

None of the above commands do anything yet. See the spec for what's
implemented when.

## Layout

See [`docs/spec.md#repository-layout`](docs/spec.md#repository-layout).

## License

MIT. See [`LICENSE`](LICENSE) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
