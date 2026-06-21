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

# Fallback drum-kit URIs, used ONLY when Live is unreachable (no socket)
# or its browser returns no loadable kits. NOTE: these synthetic
# "query:Drums#Kit-Core%20<name>" URIs do NOT resolve in a real Live
# install — load_browser_item raises "Browser item ... not found". When
# Live is reachable, the CLI resolves real "query:Drums#FileId_NNNNN" URIs
# via AbletonClient.list_drum_instruments(); this list only keeps the
# offline dry-run path producing a Plan.
_FALLBACK_INSTRUMENTS: tuple[str, ...] = (
    "query:Drums#Kit-Core%20808",
    "query:Drums#Kit-Core%20Jazz",
    "query:Drums#Kit-Core%20Kit",
)


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
) -> list[str | dict[str, str]]:
    """Resolve the instrument set handed to the planner.

    Priority: explicit ``--instruments`` (bare URIs) > a live browser walk
    of the Drums category ({name, uri} dicts with real, loadable URIs) >
    the hardcoded fallback, used only when Live is unreachable or empty.
    """
    if override:
        return list(override)
    if client is not None:
        try:
            kits = client.list_drum_instruments()
            if kits:
                sampled = _sample_kits(kits, _PLANNER_KIT_BUDGET)
                if len(sampled) < len(kits):
                    _log(
                        f"discovered {len(kits)} loadable drum kits; sampled "
                        f"{len(sampled)} across the library for the planner "
                        f"(pass --instruments to choose explicitly)"
                    )
                else:
                    _log(f"discovered {len(kits)} loadable drum kit(s) from Live")
                return sampled
            _log("Live returned no loadable drum kits; using fallback list")
        except Exception as exc:  # pragma: no cover — diagnostic path
            _log(f"browser walk failed ({exc}); using fallback list")
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
