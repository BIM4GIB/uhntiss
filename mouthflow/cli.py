"""Mouthflow CLI: `mouthflow record | run <wav> | dry-run <wav>`.

Logs progress to stderr. On ``--json`` the Plan is echoed to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import typer

from mouthflow import capture
from mouthflow.devices import get_device, get_device_by_id
from mouthflow.devices.base import DeviceSpec
from mouthflow.execute import AbletonClient, AbletonError, apply_plan
from mouthflow.plan import make_plan
from mouthflow.schemas import Plan

app = typer.Typer(add_completion=False, help="Voice-driven arrangement agent for Ableton Live.")

_DEFAULT_DEVICE = "drums"


# How many discovered kits to put in front of the planner. A real Live
# library can hold hundreds of drum racks; sending them all would bloat
# every planning call. We sample down to this budget — see _sample_kits.
_PLANNER_KIT_BUDGET = 150


def _log(msg: str) -> None:
    print(f"[mouthflow] {msg}", file=sys.stderr)


def _sample_kits(kits: list[dict[str, str]], budget: int) -> list[dict[str, str]]:
    """Even-stride sample of ``kits`` capped at ``budget``, order preserved.

    Live returns kits alphabetically, so a head-truncation would only ever
    show the planner early-letter kits. Striding spreads the sample across
    the whole library so kit *character* (the names) stays representative.
    """
    if budget <= 0 or len(kits) <= budget:
        return list(kits)
    step = len(kits) / budget
    return [kits[int(i * step)] for i in range(budget)]


def _resolve_instruments(
    override: list[str] | None,
    client: AbletonClient | None,
    spec: DeviceSpec,
) -> list[str | dict[str, str]]:
    """Resolve the instrument set handed to the planner for ``spec``.

    Priority: explicit ``--instruments`` (bare URIs) > a live browser walk
    of the device's ``browser_category`` ({name, uri} dicts with real,
    loadable URIs) > the device's fallback, used only when Live is
    unreachable or empty.
    """
    if override:
        return list(override)
    if client is not None:
        try:
            kits = client.list_instruments(
                spec.browser_category, name_filter=spec.instrument_filter
            )
            if kits:
                sampled = _sample_kits(kits, _PLANNER_KIT_BUDGET)
                if len(sampled) < len(kits):
                    _log(
                        f"discovered {len(kits)} loadable {spec.id} instruments; sampled "
                        f"{len(sampled)} across the library for the planner "
                        f"(pass --instruments to choose explicitly)"
                    )
                else:
                    _log(f"discovered {len(kits)} loadable {spec.id} instrument(s) from Live")
                return sampled
            _log(f"Live returned no loadable {spec.id} instruments; using fallback list")
        except Exception as exc:  # pragma: no cover — diagnostic path
            _log(f"browser walk failed ({exc}); using fallback list")
    return list(spec.fallback_instruments)


def _tempo_from_hint(hint: str | None) -> float | None:
    """Pull an explicit BPM out of a freeform hint, e.g. "boombap 90 bpm"."""
    if not hint:
        return None
    m = re.search(r"\b(\d{2,3})\s*bpm\b", hint, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _run_pipeline(
    wav: Path,
    *,
    client: AbletonClient | None,
    hint: str | None,
    instruments_override: list[str] | None,
    device_id: str = _DEFAULT_DEVICE,
    tempo: float | None = None,
    bar_align: bool = True,
    correct: bool = True,
    key: str | None = None,
    scale: str | None = None,
    bars="auto",
) -> Plan:
    _log(f"normalising {wav}")
    normalised = capture.from_file(wav)

    if device_id == "auto":
        from mouthflow.classify import classify

        intent, conf = classify(normalised)
        try:
            spec = get_device(intent)
        except KeyError as exc:
            raise typer.BadParameter(
                f"router classified intent={intent.value} but no device handles it"
            ) from exc
        _log(f"router: {intent.value} (conf {conf:.2f}) -> {spec.id} device")
    else:
        try:
            spec = get_device_by_id(device_id)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc

    # Explicit --tempo wins; otherwise a "NN bpm" in the hint forces tempo too.
    # (The drum device honours it; pitched/drone accept it for clip sizing.)
    forced_tempo = tempo if tempo and tempo > 0 else _tempo_from_hint(hint)

    _log(f"transcribing ({spec.id})")
    transcription = spec.transcriber.transcribe(normalised, tempo=forced_tempo, bar_align=bar_align)
    forced = " (forced)" if forced_tempo else ""
    _log(f"  tempo={transcription.tempo_bpm:.1f} BPM{forced}, notes={len(transcription.hits)}")

    # Pitched post-processing: scale-snap (autotune-style) + fit to a whole bar
    # count that loops cleanly on the grid. Drums pass through untouched.
    from mouthflow import refine as _refine

    transcription, refine_meta = _refine.refine_transcription(
        transcription, spec.clip_mode, correct=correct, key=key, scale=scale, bars=bars,
    )
    if refine_meta["key"]:
        _log(f"  note correction -> {refine_meta['key']} ({'forced' if key else 'auto'})")
    if refine_meta["bars"]:
        _log(f"  fit to {refine_meta['bars']} bars (loops on the grid)")

    instruments = _resolve_instruments(instruments_override, client, spec)
    plan = make_plan(
        transcription,
        session_state={"available_instruments": instruments},
        user_hint=hint,
        device=spec,
    )
    # The chosen bar count wins over the planner's length guess so the clip loops.
    if refine_meta["bars"]:
        for clip in plan.clips:
            clip.length_bars = float(refine_meta["bars"])
    # Device-produced automation (e.g. the drone loudness contour) is attached
    # to the plan's clip(s); the LLM never sees or generates it.
    if transcription.automation:
        for clip in plan.clips:
            clip.automation = transcription.automation
    _log(f"plan: {plan.rationale}")
    return plan


def _emit_or_apply(plan: Plan, *, json_out: bool, client: AbletonClient | None) -> None:
    if json_out:
        print(plan.model_dump_json(indent=2))
    if client is not None:
        _log("applying to Ableton")
        apply_plan(plan, client)
        _log("done")


def _parse_instruments(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


_DEVICE_HELP = "Which voice to transcribe: drums | bass | lead | drone | auto (route by ear)."
_BAR_ALIGN_HELP = "Snap to the bar grid (tighter fit) vs the performance's timing."
_CORRECT_HELP = "Note correction (pitched voices): snap notes to a scale so wobbly takes land in tune."
_KEY_HELP = "Force the key for note correction, e.g. C, F#, Bb (default: auto-detect from the take)."
_SCALE_HELP = "Scale for note correction: major|minor|dorian|harmonic_minor|major_pentatonic|minor_pentatonic|chromatic (default: auto)."
_BARS_HELP = "Fit the clip to a whole bar count so it loops on the grid: auto | off | 4 | 8 | 16."


@app.command()
def record(
    duration: float = typer.Option(15.0, help="Recording length in seconds."),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    input: int | None = typer.Option(None, "--input", help="Input device index (see `input-devices`)."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint", help="Optional freeform hint to the planner."),
    tempo: float | None = typer.Option(
        None, "--tempo", help="Force tempo in BPM (skips detection). Also reads 'NN bpm' from --hint."
    ),
    bar_align: bool = typer.Option(True, "--bar-align/--no-bar-align", help=_BAR_ALIGN_HELP),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    instruments: str | None = typer.Option(
        None, "--instruments", help="Comma-separated browser URIs. Overrides session lookup."
    ),
    json_out: bool = typer.Option(False, "--json", help="Echo the Plan as JSON to stdout."),
) -> None:
    """Capture audio, run the pipeline, apply to Ableton."""
    _log(f"recording {duration}s")
    wav = capture.record(duration, input_device=input)
    with AbletonClient(host, port) as client:
        plan = _run_pipeline(
            wav,
            client=client,
            hint=hint,
            instruments_override=_parse_instruments(instruments),
            device_id=device,
            tempo=tempo,
            bar_align=bar_align,
            correct=correct,
            key=key,
            scale=scale,
            bars=bars,
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command("record-stream")
def record_stream(
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    input: int | None = typer.Option(None, "--input", help="Input device index (see `input-devices`)."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint"),
    tempo: float | None = typer.Option(None, "--tempo", help="Force tempo in BPM (else the project tempo)."),
    bar_align: bool = typer.Option(True, "--bar-align/--no-bar-align", help=_BAR_ALIGN_HELP),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Record the mic until 'stop' arrives on stdin, then transcribe + apply.

    The device's start/stop button drives this: spawning the command starts
    recording, writing 'stop' to stdin finishes it. The take's length is the
    performer's, not a fixed timer; the project tempo is fetched so it fits the
    grid (like transcribe-clip).
    """
    import sys as _sys
    import threading

    stop = threading.Event()

    def _watch_stdin() -> None:
        try:
            for line in _sys.stdin:
                if line.strip().lower() in ("stop", "q", "quit"):
                    break
        except (ValueError, OSError):
            pass
        stop.set()  # explicit stop, or stdin closed

    threading.Thread(target=_watch_stdin, daemon=True).start()
    _log("recording — send 'stop' to finish (or close stdin)")
    wav = capture.record_until_stop(stop.is_set, input_device=input)
    _log("stopped — transcribing")
    with AbletonClient(host, port) as client:
        if tempo is None:
            try:
                session = client.get_session_info()
                proj = session.get("tempo") if isinstance(session, dict) else None
                if proj:
                    tempo = float(proj)
                    _log(f"using project tempo {tempo:.1f} BPM")
            except (AbletonError, OSError):
                pass
        plan = _run_pipeline(
            wav,
            client=client,
            hint=hint,
            instruments_override=_parse_instruments(instruments),
            device_id=device,
            tempo=tempo,
            bar_align=bar_align,
            correct=correct,
            key=key,
            scale=scale,
            bars=bars,
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command()
def run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint"),
    tempo: float | None = typer.Option(None, "--tempo", help="Force tempo in BPM (skips detection)."),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the pipeline on an existing WAV and apply to Ableton."""
    with AbletonClient(host, port) as client:
        plan = _run_pipeline(
            wav,
            client=client,
            hint=hint,
            instruments_override=_parse_instruments(instruments),
            device_id=device,
            tempo=tempo,
            correct=correct,
            key=key,
            scale=scale,
            bars=bars,
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command("dry-run")
def dry_run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    hint: str | None = typer.Option(None, "--hint"),
    tempo: float | None = typer.Option(None, "--tempo", help="Force tempo in BPM (skips detection)."),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json", help="Echo the Plan as JSON to stdout."),
) -> None:
    """Run the pipeline, print the Plan, don't touch Live.

    Without ``--json`` only the human-readable rationale is logged. Pass
    ``--json`` to get the full Plan on stdout for downstream tooling.
    """
    if not json_out:
        # Dry-run without an output flag is nearly useless; default to JSON.
        json_out = True
    plan = _run_pipeline(
        wav,
        client=None,
        hint=hint,
        instruments_override=_parse_instruments(instruments),
        device_id=device,
        tempo=tempo,
        correct=correct,
        key=key,
        scale=scale,
        bars=bars,
    )
    _emit_or_apply(plan, json_out=json_out, client=None)


@app.command()
def doctor(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
) -> None:
    """Preflight checks for a first end-to-end run.

    Verifies ANTHROPIC_API_KEY is set, AbletonMCP is reachable on the
    socket, and the Drums browser returns loadable kits. Exits non-zero if
    any check fails, so it's safe to chain in scripts.
    """
    failures: list[str] = []

    if os.environ.get("ANTHROPIC_API_KEY"):
        _log("ok   ANTHROPIC_API_KEY is set")
    else:
        failures.append("ANTHROPIC_API_KEY not set")
        _log("FAIL ANTHROPIC_API_KEY not set")

    # OSError covers ConnectionRefusedError / socket timeouts when Live
    # isn't running or the Remote Script isn't loaded; AbletonError covers
    # a reachable socket that replies with status=error.
    try:
        with AbletonClient(host, port) as client:
            info = client.get_session_info()
            tempo = info.get("tempo") if isinstance(info, dict) else None
            detail = f"tempo {tempo}" if tempo is not None else "no session info"
            _log(f"ok   AbletonMCP reachable at {host}:{port} ({detail})")
            try:
                kits = client.list_drum_instruments()
            except (AbletonError, OSError) as exc:
                failures.append(f"browser traversal failed: {exc}")
                _log(f"FAIL browser traversal: {exc}")
            else:
                if kits:
                    first = kits[0]
                    name = first.get("name") if isinstance(first, dict) else first
                    _log(f"ok   {len(kits)} drum kit(s) discovered; first: {name}")
                else:
                    failures.append("no loadable drum kits found in browser")
                    _log("FAIL no loadable drum kits found in browser")
    except (AbletonError, OSError) as exc:
        failures.append(f"AbletonMCP not reachable at {host}:{port}: {exc}")
        _log(f"FAIL AbletonMCP not reachable at {host}:{port}: {exc}")

    if failures:
        _log(f"{len(failures)} check(s) failed")
        raise typer.Exit(code=1)
    _log("all checks passed — ready to run")


@app.command("input-devices")
def input_devices() -> None:
    """Print input-capable audio devices as JSON (for the M4L mic picker)."""
    print(json.dumps(capture.list_input_devices()))


@app.command("transcribe-clip")
def transcribe_clip(
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint"),
    tempo: float | None = typer.Option(None, "--tempo"),
    bar_align: bool = typer.Option(True, "--bar-align/--no-bar-align", help=_BAR_ALIGN_HELP),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Transcribe the audio clip currently SELECTED in Live (no mic).

    Asks the bridge for the detail-view clip's sample file and runs the normal
    pipeline on it. Requires the forked Remote Script command
    ``get_selected_clip`` (see ``bridge/``).
    """
    with AbletonClient(host, port) as client:
        try:
            info = client.get_selected_clip()
        except (AbletonError, OSError) as exc:
            _log(f"could not read the selected clip ({exc}); is the forked bridge installed?")
            raise typer.Exit(code=1)
        path = info.get("file_path") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("is_audio") and path):
            raise typer.BadParameter("select an AUDIO clip in Live's detail view first")
        _log(f"selected clip: {info.get('name')} -> {path}")
        # Default to the project tempo so the transcription lands exactly on the
        # set's grid (the clip was recorded at it). --tempo still overrides.
        if tempo is None:
            try:
                session = client.get_session_info()
                proj_tempo = session.get("tempo") if isinstance(session, dict) else None
                if proj_tempo:
                    tempo = float(proj_tempo)
                    _log(f"using project tempo {tempo:.1f} BPM")
            except (AbletonError, OSError):
                pass
        plan = _run_pipeline(
            Path(path),
            client=client,
            hint=hint,
            instruments_override=_parse_instruments(instruments),
            device_id=device,
            tempo=tempo,
            bar_align=bar_align,
            correct=correct,
            key=key,
            scale=scale,
            bars=bars,
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command("list-kits")
def list_kits(
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    limit: int = typer.Option(0, help="Cap the result (0 = all); strided sample."),
) -> None:
    """Print loadable instruments for a device from the running Live set as JSON.

    Emits a JSON array of ``{"name", "uri"}`` to stdout — machine-facing,
    for UIs (e.g. the Max for Live device's instrument picker). Defaults to the
    drums device's browser category; ``--device`` selects another voice. Exits
    non-zero if Live is unreachable.
    """
    try:
        spec = get_device_by_id(device)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        with AbletonClient(host, port) as client:
            kits = client.list_instruments(spec.browser_category, name_filter=spec.instrument_filter)
    except (AbletonError, OSError) as exc:
        _log(f"FAIL AbletonMCP not reachable at {host}:{port}: {exc}")
        raise typer.Exit(code=1)
    if limit and limit > 0:
        kits = _sample_kits(kits, limit)
    print(json.dumps(kits))


if __name__ == "__main__":
    app()
