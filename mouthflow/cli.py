"""Mouthflow CLI: `mouthflow record | run <wav> | dry-run <wav>`.

Logs progress to stderr. On ``--json`` the Plan is echoed to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
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


_STATE_DIR = Path.home() / ".mouthflow"
_LAST_TAKE = _STATE_DIR / "last_take.json"

# Kit-list cache. The browser walk costs seconds per take (measured 7.0s for
# Drums) and libraries change rarely; cache per category with a TTL and warm
# the cache on a separate connection WHILE the take is being recorded.
_KIT_CACHE_TTL_S = 24 * 3600.0


def _kit_cache_path(category: str) -> Path:
    # Keyed by browser category; the stored list is post-instrument_filter.
    # Today every device has a distinct category, so no filter collisions.
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", category)
    return _STATE_DIR / f"kits-{safe}.json"


def _read_kit_cache(category: str) -> list[dict] | None:
    """Fresh cached kit list for ``category``, or None.

    Defensive about shape: a hand-edited or corrupt file must degrade to a
    live walk, never crash a take after the performance.
    """
    try:
        data = json.loads(_kit_cache_path(category).read_text())
        if not isinstance(data, dict):
            return None
        kits = data.get("kits")
        if (
            isinstance(kits, list) and kits
            and time.time() - float(data.get("ts", 0)) <= _KIT_CACHE_TTL_S
        ):
            return kits
    except (OSError, ValueError, TypeError):
        pass
    return None


def _write_kit_cache(category: str, kits: list[dict]) -> None:
    import os
    import tempfile

    tmp = None
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        # Atomic replace: the warm thread and a concurrent CLI must never
        # leave a half-written file for a reader to trip over.
        fd, tmp = tempfile.mkstemp(dir=_STATE_DIR, prefix=".kits-")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"ts": time.time(), "kits": kits}))
        os.replace(tmp, _kit_cache_path(category))
        tmp = None
    except OSError:
        pass  # cache is an optimisation; never let it kill a take
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)  # don't orphan the temp file on a failed write
            except OSError:
                pass


def _maybe_warm(instruments_override: str | None, device: str, host: str, port: int):
    """Kick off a background kit-cache refresh when the take will need one.

    Skipped for an explicit --instruments override and for --device auto
    (the voice isn't known yet). A bad --device id surfaces later with the
    proper error, so it's silently skipped here.
    """
    if instruments_override or device == "auto":
        return None
    try:
        return _warm_kit_cache(get_device_by_id(device), host, port)
    except KeyError:
        return None


def _warm_kit_cache(spec: DeviceSpec, host: str, port: int):
    """Refresh the kit cache in the background, on its OWN connection.

    Kicked off before/while the mic records, so by the time the planner needs
    the list it's a file read instead of a multi-second browser walk. Uses a
    separate socket — the main client is mid-conversation and the protocol is
    strictly request/response. Best-effort: any failure just means the
    resolve step does its own live walk.
    """
    import threading

    def work() -> None:
        try:
            with AbletonClient(host, port) as c:
                kits = c.list_instruments(spec.browser_category, name_filter=spec.instrument_filter)
            if kits:
                # Silent on purpose: a log line from this thread lands mid-take
                # and hijacks the M4L device's single status comment.
                _write_kit_cache(spec.browser_category, kits)
        except Exception:
            pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def _save_last_take(wav: Path, **params) -> None:
    """Remember the take + its flags so ``retry-last`` can replay it.

    Bookkeeping must never kill a take, so failures are swallowed.
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _LAST_TAKE.write_text(json.dumps({"wav": str(wav), **params}, indent=2))
    except OSError:
        pass


def _connect_or_fail(host: str, port: int) -> AbletonClient:
    """Open the Ableton socket BEFORE any recording starts.

    Failing fast here means a dead Live session costs zero performances —
    the old ordering recorded first and lost the take to a raw traceback.
    """
    client = AbletonClient(host, port)
    try:
        client.connect()
    except OSError as exc:
        _log(f"FAIL Ableton not reachable at {host}:{port} ({exc})")
        _log("start Live with the AbletonMCP Remote Script, or use `dry-run` to skip Live")
        raise typer.Exit(code=1)
    return client


def _project_tempo(client: AbletonClient | None) -> float | None:
    """The running Live set's tempo, or None if unavailable."""
    if client is None:
        return None
    try:
        session = client.get_session_info()
    except (AbletonError, OSError):
        return None
    proj = session.get("tempo") if isinstance(session, dict) else None
    try:
        return float(proj) if proj else None
    except (TypeError, ValueError):
        return None


def _count_in(seconds: int) -> None:
    """Audible-in-the-panel count-in, emitted right before capture opens.

    Runs after the process is fully imported, so — unlike a count-in in the
    M4L glue — the "go" line lands when recording is actually about to start
    instead of ~0.5s before it (which clipped the take's first hit).
    """
    for i in range(int(seconds), 0, -1):
        _log(f"count-in {i}")
        time.sleep(1.0)
    _log("REC — go!")


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

    Priority: explicit ``--instruments`` (bare URIs) > a fresh kit cache
    (written by a previous walk or the capture-time warm thread) > a live
    browser walk of the device's ``browser_category`` > the device's
    fallback, used only when Live is unreachable or empty.
    """
    if override:
        return list(override)
    cached = _read_kit_cache(spec.browser_category)
    if cached:
        sampled = _sample_kits(cached, _PLANNER_KIT_BUDGET)
        _log(f"using cached kit list ({len(cached)} {spec.id} instruments)")
        return sampled
    if client is not None:
        try:
            kits = client.list_instruments(
                spec.browser_category, name_filter=spec.instrument_filter
            )
            if kits:
                _write_kit_cache(spec.browser_category, kits)
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
    warm_thread=None,
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
    t0 = time.perf_counter()
    transcription = spec.transcriber.transcribe(normalised, tempo=forced_tempo, bar_align=bar_align)
    forced = " (forced)" if forced_tempo else ""
    unit = "hits" if spec.clip_mode.value == "percussive" else "notes"
    _log(
        f"heard {len(transcription.hits)} {unit} @ {transcription.tempo_bpm:.1f} BPM{forced}"
        f" ({time.perf_counter() - t0:.1f}s)"
    )

    # Zero-notes guard: a silent/breath-only take (or a dead mic) must not
    # burn an LLM call and land an empty clip on a junk track. The take WAV is
    # kept, so `retry-last` can replay it after fixing the input.
    if not transcription.hits:
        _log("heard nothing usable — check the input device and level (see `input-devices`)")
        _log(f"take kept at {wav}")
        raise typer.Exit(code=3)

    # Pitched post-processing: scale-snap (autotune-style) + fit to a whole bar
    # count that loops cleanly on the grid. Drums pass through untouched.
    from mouthflow import refine as _refine

    transcription, refine_meta = _refine.refine_transcription(
        transcription, spec.clip_mode, correct=correct, key=key, scale=scale, bars=bars,
    )
    if refine_meta["key"]:
        _log(f"  note correction -> {refine_meta['key']} ({'forced' if key else 'auto'})")
    if refine_meta.get("key_skipped"):
        _log(f"  --key/--scale ignored: {refine_meta['key_skipped']}")
    if refine_meta["bars"]:
        _log(f"  fit to {refine_meta['bars']} bars (loops on the grid)")

    # Give the capture-time cache warmer time to land its file — but only
    # when there's no usable cache yet (the first take of a session). Later
    # takes proceed immediately on the previous list while the refresh lands.
    # The wait is generous: bailing early would just start an IDENTICAL walk
    # on the main client, contending with the warmer for the Remote Script.
    if warm_thread is not None and _read_kit_cache(spec.browser_category) is None:
        warm_thread.join(timeout=30.0)
    instruments = _resolve_instruments(instruments_override, client, spec)

    t0 = time.perf_counter()
    plan = make_plan(
        transcription,
        session_state={"available_instruments": instruments},
        user_hint=hint,
        device=spec,
    )
    plan_s = time.perf_counter() - t0
    # The chosen bar count wins over the planner's length guess so the clip loops.
    if refine_meta["bars"]:
        for clip in plan.clips:
            clip.length_bars = float(refine_meta["bars"])
    # Device-produced automation (e.g. the drone loudness contour) is attached
    # to the plan's clip(s); the LLM never sees or generates it.
    if transcription.automation:
        for clip in plan.clips:
            clip.automation = transcription.automation
    _log(f"plan: {plan.rationale} ({plan_s:.1f}s)")
    return plan


def _emit_or_apply(
    plan: Plan, *, json_out: bool, client: AbletonClient | None, set_tempo: bool = False
) -> None:
    if json_out:
        print(plan.model_dump_json(indent=2))
    if client is not None:
        _log("applying to Ableton")
        t0 = time.perf_counter()
        try:
            apply_plan(plan, client, set_tempo=set_tempo)
        except AbletonError as exc:
            # A cached kit URI can outlive the kit (pack removed/renamed) and
            # only fails HERE, after the performance and the LLM call.
            # Invalidate the caches so the retry resolves a fresh list.
            for stale in _STATE_DIR.glob("kits-*.json"):
                try:
                    stale.unlink()
                except OSError:
                    pass
            _log(f"apply failed ({exc}); kit caches invalidated — `mouthflow retry-last` will re-resolve")
            raise typer.Exit(code=1)
        _log(f"done ({time.perf_counter() - t0:.1f}s)")


def _parse_instruments(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


_DEVICE_HELP = "Which voice to transcribe: drums | bass | lead | drone | auto (route by ear)."
_BAR_ALIGN_HELP = "Snap to the bar grid (tighter fit) vs the performance's timing."
_CORRECT_HELP = "Note correction (pitched voices): snap notes to a scale so wobbly takes land in tune."
_KEY_HELP = "Force the key for note correction, e.g. C, F#, Bb (default: auto-detect from the take)."
_SCALE_HELP = "Scale for note correction: major|minor|dorian|harmonic_minor|major_pentatonic|minor_pentatonic|chromatic (default: auto)."
_BARS_HELP = "Fit the clip to a whole bar count so it loops on the grid: auto | off | 1 | 2 | 4 | 8 | 16."
_SET_TEMPO_HELP = (
    "Set the Live set's tempo from the take. On record/record-stream this also "
    "enables tempo detection (otherwise the take would just echo the project tempo back)."
)
_DETECT_TEMPO_HELP = (
    "Detect the tempo from the take instead of using the project tempo "
    "(for solo takes not performed against the set's grid)."
)


def _validate_device(device: str) -> None:
    """Reject a bad --device BEFORE the mic opens (a typo must not cost a take)."""
    if device == "auto":
        return
    try:
        get_device_by_id(device)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc


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
    countin: int = typer.Option(
        0, "--countin", help="Count-in seconds before recording starts (logged per second)."
    ),
    bar_align: bool = typer.Option(True, "--bar-align/--no-bar-align", help=_BAR_ALIGN_HELP),
    correct: bool = typer.Option(True, "--correct/--no-correct", help=_CORRECT_HELP),
    key: str | None = typer.Option(None, "--key", help=_KEY_HELP),
    scale: str | None = typer.Option(None, "--scale", help=_SCALE_HELP),
    bars: str = typer.Option("auto", "--bars", help=_BARS_HELP),
    set_tempo: bool = typer.Option(False, "--set-tempo/--no-set-tempo", help=_SET_TEMPO_HELP),
    detect_tempo: bool = typer.Option(False, "--detect-tempo", help=_DETECT_TEMPO_HELP),
    instruments: str | None = typer.Option(
        None, "--instruments", help="Comma-separated browser URIs. Overrides session lookup."
    ),
    json_out: bool = typer.Option(False, "--json", help="Echo the Plan as JSON to stdout."),
) -> None:
    """Capture audio, run the pipeline, apply to Ableton."""
    _validate_device(device)
    with _connect_or_fail(host, port) as client:
        # One clock: the Live set owns tempo. Record against the project's
        # grid unless --tempo / an 'NN bpm' hint / --detect-tempo overrides.
        # --set-tempo implies detection: pushing the project tempo back to
        # the project would be a no-op.
        if tempo is None and not detect_tempo and not set_tempo and _tempo_from_hint(hint) is None:
            proj = _project_tempo(client)
            if proj:
                tempo = proj
                _log(f"using project tempo {tempo:.1f} BPM")
        # Refresh the kit cache WHILE we record — the planner then reads a
        # file instead of waiting seconds for a serialized browser walk.
        warm = _maybe_warm(instruments, device, host, port)
        if countin > 0:
            _count_in(countin)
        _log(f"recording {duration}s")
        wav = capture.record(duration, input_device=input)
        _save_last_take(
            wav, device=device, hint=hint, tempo=tempo, bar_align=bar_align,
            correct=correct, key=key, scale=scale, bars=bars,
            instruments=instruments, set_tempo=set_tempo,
        )
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
            warm_thread=warm,
        )
        _emit_or_apply(plan, json_out=json_out, client=client, set_tempo=set_tempo)


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
    set_tempo: bool = typer.Option(False, "--set-tempo/--no-set-tempo", help=_SET_TEMPO_HELP),
    detect_tempo: bool = typer.Option(False, "--detect-tempo", help=_DETECT_TEMPO_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Record the mic until 'stop' arrives on stdin, then transcribe + apply.

    The device's start/stop button drives this: spawning the command starts
    recording, writing 'stop' to stdin finishes it. The take's length is the
    performer's, not a fixed timer; the project tempo is fetched (BEFORE the
    take, so a dead Live session costs no performance) so it fits the grid.
    """
    import sys as _sys
    import threading

    _validate_device(device)
    with _connect_or_fail(host, port) as client:
        if tempo is None and not detect_tempo and not set_tempo and _tempo_from_hint(hint) is None:
            proj = _project_tempo(client)
            if proj:
                tempo = proj
                _log(f"using project tempo {tempo:.1f} BPM")

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
        warm = _maybe_warm(instruments, device, host, port)
        _log("recording — send 'stop' to finish (or close stdin)")
        wav = capture.record_until_stop(
            stop.is_set, input_device=input,
            on_level=lambda db: _log(f"level {db:.1f}"),
        )
        _log("stopped — transcribing")
        _save_last_take(
            wav, device=device, hint=hint, tempo=tempo, bar_align=bar_align,
            correct=correct, key=key, scale=scale, bars=bars,
            instruments=instruments, set_tempo=set_tempo,
        )
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
            warm_thread=warm,
        )
        _emit_or_apply(plan, json_out=json_out, client=client, set_tempo=set_tempo)


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
    set_tempo: bool = typer.Option(False, "--set-tempo/--no-set-tempo", help=_SET_TEMPO_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the pipeline on an existing WAV and apply to Ableton."""
    with _connect_or_fail(host, port) as client:
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
            warm_thread=_maybe_warm(instruments, device, host, port),
        )
        _emit_or_apply(plan, json_out=json_out, client=client, set_tempo=set_tempo)


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


def _api_key_from_env_file() -> str | None:
    """Read ANTHROPIC_API_KEY from a repo/cwd ``.env``.

    The M4L glue reads ``.env`` directly (it never sees a login shell), so
    doctor must agree with what the device will actually do — an unsourced
    shell shouldn't fail a check the device would pass.
    """
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"ANTHROPIC_API_KEY\s*=\s*[\"']?([^\"'\r\n]+)", text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


# Forked bridge commands and harmless probe params. A stock bridge answers
# "Unknown command …"; any other reply (ok or a param error) proves the
# command is spliced in.
_BRIDGE_PROBES: tuple[tuple[str, dict], ...] = (
    ("get_selected_clip", {}),
    ("set_clip_envelope", {"track_index": -1, "clip_index": 0, "device_index": 0,
                           "parameter": "", "steps": []}),
)


def _probe_bridge(client: AbletonClient, failures: list[str]) -> None:
    """Probe the forked bridge commands; append misses/errors to ``failures``.

    Classification: a transport failure means we couldn't ask (FAIL, but not
    "missing"); a status=error mentioning an unknown command means stock
    bridge (missing); any other reply — ok or a param complaint — proves the
    handler exists.
    """
    from mouthflow.execute import AbletonTransportError

    for cmd, params in _BRIDGE_PROBES:
        try:
            client.send_command(cmd, params)
        except AbletonTransportError as exc:
            failures.append(f"bridge probe {cmd} failed: {exc}")
            _log(f"FAIL bridge probe {cmd}: {exc}")
        except AbletonError as exc:
            if "unknown command" in str(exc).lower():
                failures.append(f"bridge command {cmd} missing (stock ableton-mcp; see bridge/README.md)")
                _log(f"FAIL bridge command {cmd} missing — splice the fork (bridge/README.md)")
            else:
                # A reply other than "unknown command" means the handler
                # exists and rejected our probe params.
                _log(f"ok   bridge command {cmd} present")
        else:
            _log(f"ok   bridge command {cmd} present")


@app.command()
def doctor(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    bridge: bool = typer.Option(
        False, "--bridge", help="Also probe the forked bridge commands (transcribe-clip / drone automation)."
    ),
) -> None:
    """Preflight checks for a first end-to-end run.

    Verifies ANTHROPIC_API_KEY is available (env or .env), AbletonMCP is
    reachable on the socket, and the Drums browser returns loadable kits.
    ``--bridge`` additionally probes for the forked Remote Script commands.
    Exits non-zero if any check fails, so it's safe to chain in scripts.
    """
    failures: list[str] = []

    if os.environ.get("ANTHROPIC_API_KEY"):
        _log("ok   ANTHROPIC_API_KEY is set")
    elif _api_key_from_env_file():
        _log("ok   ANTHROPIC_API_KEY found in .env (the device reads it from there too)")
    else:
        failures.append("ANTHROPIC_API_KEY not set")
        _log("FAIL ANTHROPIC_API_KEY not set (env or .env)")

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
            if bridge:
                _probe_bridge(client, failures)
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
    set_tempo: bool = typer.Option(False, "--set-tempo/--no-set-tempo", help=_SET_TEMPO_HELP),
    instruments: str | None = typer.Option(None, "--instruments"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Transcribe the audio clip currently SELECTED in Live (no mic).

    Asks the bridge for the detail-view clip's sample file and runs the normal
    pipeline on it. Requires the forked Remote Script command
    ``get_selected_clip`` (see ``bridge/``).
    """
    with _connect_or_fail(host, port) as client:
        try:
            info = client.get_selected_clip()
        except (AbletonError, OSError) as exc:
            _log(f"could not read the selected clip ({exc}); is the forked bridge installed?")
            raise typer.Exit(code=1)
        path = info.get("file_path") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("is_audio") and path):
            raise typer.BadParameter("select an AUDIO clip in Live's detail view first")
        _log(f"selected clip: {info.get('name')} -> {path}")
        # Bookkeep like record does, so retry-last (and post-hoc diagnosis of
        # "that sounded wrong") works for clip transcriptions too.
        _save_last_take(
            Path(path), device=device, hint=hint, tempo=tempo, bar_align=bar_align,
            correct=correct, key=key, scale=scale, bars=bars,
            instruments=instruments, set_tempo=set_tempo,
        )
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
            warm_thread=_maybe_warm(instruments, device, host, port),
        )
        _emit_or_apply(plan, json_out=json_out, client=client, set_tempo=set_tempo)


@app.command("retry-last")
def retry_last(
    device: str | None = typer.Option(
        None, "--device",
        help="Override the saved voice — also handy to re-transcribe the same take as another voice.",
    ),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(9877),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Replay the most recent take through the pipeline (no re-performing).

    Every `record`/`record-stream` take is kept in ``~/.mouthflow/takes/`` with
    its flags in ``last_take.json`` — so a failure after the performance
    (Ableton hiccup, API error) costs a retry, not a re-take.
    """
    try:
        state = json.loads(_LAST_TAKE.read_text())
    except (OSError, ValueError):
        _log(f"no saved take found at {_LAST_TAKE} — record one first")
        raise typer.Exit(code=1)
    wav = Path(state.get("wav", ""))
    if not wav.exists():
        _log(f"saved take is gone: {wav}")
        raise typer.Exit(code=1)
    device_id = device or state.get("device", _DEFAULT_DEVICE)
    _validate_device(device_id)
    # A saved instrument list belongs to the saved voice's browser category;
    # when the voice is overridden, let the live walk resolve fresh ones.
    saved_instruments = state.get("instruments") if device_id == state.get("device") else None
    _log(f"retrying take {wav.name} (device {device_id})")
    with _connect_or_fail(host, port) as client:
        plan = _run_pipeline(
            wav,
            client=client,
            hint=state.get("hint"),
            instruments_override=_parse_instruments(saved_instruments),
            device_id=device_id,
            warm_thread=_maybe_warm(saved_instruments, device_id, host, port),
            tempo=state.get("tempo"),
            bar_align=state.get("bar_align", True),
            correct=state.get("correct", True),
            key=state.get("key"),
            scale=state.get("scale"),
            bars=state.get("bars", "auto"),
        )
        _emit_or_apply(
            plan, json_out=json_out, client=client, set_tempo=state.get("set_tempo", False)
        )


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
    if kits:
        _write_kit_cache(spec.browser_category, kits)  # freshen the planner's cache too
    if limit and limit > 0:
        kits = _sample_kits(kits, limit)
    print(json.dumps(kits))


if __name__ == "__main__":
    app()
