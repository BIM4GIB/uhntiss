"""Mouthflow CLI: `mouthflow record | run <wav> | dry-run <wav>`.

Logs progress to stderr. On ``--json`` the Plan is echoed to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from mouthflow import capture
from mouthflow.classify import classify
from mouthflow.execute import AbletonClient, apply_plan
from mouthflow.plan import make_plan
from mouthflow.schemas import Intent, Plan
from mouthflow.transcribe import transcribe_drums

app = typer.Typer(add_completion=False, help="Voice-driven arrangement agent for Ableton Live.")

# Fallback drum rack URIs used when the caller doesn't supply --instruments
# and get_session_info doesn't include any. These are common Live Core
# Library kits; real projects should pass --instruments to pick from the
# actual set. Values mirror ableton-mcp's get_browser_items_at_path output.
_FALLBACK_INSTRUMENTS: tuple[str, ...] = (
    "query:Drums#Kit-Core%20808",
    "query:Drums#Kit-Core%20Jazz",
    "query:Drums#Kit-Core%20Kit",
)


def _log(msg: str) -> None:
    print(f"[mouthflow] {msg}", file=sys.stderr)


def _resolve_instruments(
    override: list[str] | None,
    client: AbletonClient | None,
) -> list[str]:
    if override:
        return override
    if client is not None:
        try:
            info = client.get_session_info()
            got = info.get("available_instruments") or info.get("instruments") or []
            if got:
                return list(got)
        except Exception as exc:  # pragma: no cover — diagnostic path
            _log(f"get_session_info failed, using fallback: {exc}")
    return list(_FALLBACK_INSTRUMENTS)


def _run_pipeline(
    wav: Path,
    *,
    client: AbletonClient | None,
    hint: str | None,
    instruments_override: list[str] | None,
) -> Plan:
    _log(f"normalising {wav}")
    normalised = capture.from_file(wav)

    intent, _conf = classify(normalised)
    if intent != Intent.DRUM:
        raise typer.BadParameter(f"v0.1 only handles DRUM intent; got {intent}")

    _log("transcribing drums")
    transcription = transcribe_drums(normalised)
    _log(f"  tempo={transcription.tempo_bpm:.1f} BPM, hits={len(transcription.hits)}")

    instruments = _resolve_instruments(instruments_override, client)
    plan = make_plan(
        transcription,
        session_state={"available_instruments": instruments},
        user_hint=hint,
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


@app.command()
def record(
    duration: float = typer.Option(15.0, help="Recording length in seconds."),
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
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command()
def run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
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
        )
        _emit_or_apply(plan, json_out=json_out, client=client)


@app.command("dry-run")
def dry_run(
    wav: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
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
    )
    _emit_or_apply(plan, json_out=json_out, client=None)


if __name__ == "__main__":
    app()
