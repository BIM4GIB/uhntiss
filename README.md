# Mouthflow

A voice-driven arrangement agent for Ableton Live. Make a sound with your
voice, get it into your session — as **drums**, **bass**, **lead**, or an
**ambient drone**.

Status: **umbrella product — a shared voice→MIDI→Ableton engine with a device
registry. Four voices ship (drums/bass/lead/drone); drum behaviour is unchanged
and byte-identical. Next: confirm Live browser categories at runtime + install
the drone automation bridge.**
**Picking up work? Start with [`docs/handover.md`](docs/handover.md).**
See also [`docs/spec.md`](docs/spec.md) (full spec),
[`docs/corpus.md`](docs/corpus.md) (labelling convention), and
[`docs/mac-handoff.md`](docs/mac-handoff.md) (first-run setup on the Mac).

> Repo codename: `uhntiss`. Project name `mouthflow` is a placeholder —
> rename before the first public release if something better shows up.

## Quickstart

Prereqs: Python 3.11, [uv](https://docs.astral.sh/uv/), Ableton Live 11+,
[ableton-mcp](https://github.com/ahujasid/ableton-mcp) installed as a Live
Remote Script, and an `ANTHROPIC_API_KEY` in your environment.

```bash
uv sync
uv run mouthflow record --device bass     # 15s capture → pipeline → applied to Live
uv run mouthflow run clip.wav --device drone   # skip the capture step
uv run mouthflow dry-run clip.wav --device lead --json  # pipeline, print Plan, don't touch Live
```

`--device` is one of `drums | bass | lead | drone` (default `drums`), or
`auto` to route by ear. See [`docs/handover.md`](docs/handover.md) for the
current state of each voice.

## Layout

See [`docs/spec.md#repository-layout`](docs/spec.md#repository-layout).

## License

MIT. See [`LICENSE`](LICENSE) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
