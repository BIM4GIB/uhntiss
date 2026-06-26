"""Mouthflow CLI: `mouthflow record | run <wav> | dry-run <wav>`.

Logs progress to stderr. On ``--json`` the Plan is echoed to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from mouthflow import capture
from mouthflow.devices import get_device_by_id
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


def _run_pipeline(
    wav: Path,
    *,
    client: AbletonClient | None,
    hint: str | None,
    instruments_override: list[str] | None,
    device_id: str = _DEFAULT_DEVICE,
) -> Plan:
    try:
        spec = get_device_by_id(device_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    _log(f"normalising {wav}")
    normalised = capture.from_file(wav)

    _log(f"transcribing ({spec.id})")
    transcription = spec.transcriber.transcribe(normalised)
    _log(f"  tempo={transcription.tempo_bpm:.1f} BPM, notes={len(transcription.hits)}")

    instruments = _resolve_instruments(instruments_override, client, spec)
    plan = make_plan(
        transcription,
        session_state={"available_instruments": instruments},
        user_hint=hint,
        device=spec,
    )
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


_DEVICE_HELP = "Which voice to transcribe: drums | bass | lead | drone."


@app.command()
def record(
    duration: float = typer.Option(15.0, help="Recording length in seconds."),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint", help="Optional freeform hint to the planner."),
    instruments: str | None = typer.Option(
        None, "--instruments", help="Comma-separated browser URIs. Overrides session lookup."
    ),
    json_out: bool = typer.Option(False, "--json", help="Echo the Plan as JSON to stdout."),
) -> None:
    """Capture audio, run the pipeline, apply to Ableton."""
    _log(f"recording {duration}s")
    wav = capture.record(duration)
    with AbletonClient(host, port) as client:
        plan = _run_pipeline(
            wav,
            client=client,
            hint=hint,
            instruments_override=_parse_instruments(instruments),
            device_id=device,
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command()
def run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    hint: str | None = typer.Option(None, "--hint"),
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
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command("dry-run")
def dry_run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    device: str = typer.Option(_DEFAULT_DEVICE, "--device", help=_DEVICE_HELP),
    hint: str | None = typer.Option(None, "--hint"),
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


@app.command("list-kits")
def list_kits(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    limit: int = typer.Option(0, help="Cap the result (0 = all); strided sample."),
) -> None:
    """Print loadable drum kits from the running Live set as JSON.

    Emits a JSON array of ``{"name", "uri"}`` to stdout — machine-facing,
    for UIs (e.g. the Max for Live device's kit picker). Exits non-zero if
    Live is unreachable.
    """
    try:
        with AbletonClient(host, port) as client:
            kits = client.list_drum_instruments()
    except (AbletonError, OSError) as exc:
        _log(f"FAIL AbletonMCP not reachable at {host}:{port}: {exc}")
        raise typer.Exit(code=1)
    if limit and limit > 0:
        kits = _sample_kits(kits, limit)
    print(json.dumps(kits))


if __name__ == "__main__":
    app()
