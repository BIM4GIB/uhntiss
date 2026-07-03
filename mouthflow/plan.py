"""Arrangement planner: single Claude call producing a validated Plan.

The planner frames Claude as a producer picking a drum kit for a
transcribed pattern. Input is a terse summary of the transcription plus
the list of browser-URI instruments available in the current Live set.
Output is a ``Plan`` forced into shape via tool-use with a strict
JSON schema derived from pydantic.

The system prompt lives in ``prompts/plan.md`` so prompt changes don't
touch code — per spec.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
from pydantic import BaseModel, Field, ValidationError

from mouthflow.schemas import ClipPlan, Plan, Transcription

if TYPE_CHECKING:
    from mouthflow.devices.base import DeviceSpec

# Sonnet-tier: the kit pick is a small, latency-sensitive structured call in
# the take's hot path. NOTE (Sonnet 5 API surface): non-default sampling
# params (temperature/top_p/top_k) are REJECTED, and thinking is adaptive-on
# when the field is omitted — we disable it explicitly to keep the call snappy.
DEFAULT_MODEL = "claude-sonnet-5"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "plan.md"


class _LLMClipPlan(BaseModel):
    track_name: str = Field(..., description="Display name for the new MIDI track.")
    instrument_path: str = Field(
        ..., description="Browser URI of the chosen instrument — must come from available_instruments."
    )
    length_bars: float = Field(..., gt=0)


class _LLMPlan(BaseModel):
    tempo: float = Field(..., gt=0)
    clips: list[_LLMClipPlan] = Field(..., min_length=1)
    rationale: str


@lru_cache(maxsize=None)
def _system_prompt(path: Path = _PROMPT_PATH) -> str:
    # Keyed by path so multiple devices' prompts can be cached in one process
    # (the Max for Live device runs e.g. bass then drum back-to-back).
    return Path(path).read_text(encoding="utf-8")


def _tool_schema() -> dict[str, Any]:
    schema = _LLMPlan.model_json_schema()
    return {
        "name": "emit_plan",
        "description": "Return the arrangement plan for the transcribed pattern.",
        "input_schema": schema,
    }


def _hit_histogram(transcription: Transcription) -> dict[str, int]:
    names = {36: "kick", 38: "snare", 42: "hat_closed", 46: "hat_open", 39: "perc"}
    counts = Counter(names.get(h.midi_note, str(h.midi_note)) for h in transcription.hits)
    return dict(counts)


def _normalise_instruments(available: list) -> list[dict[str, str]]:
    """Coerce ``available_instruments`` to ``[{name, uri}]``.

    The session list arrives in two shapes: bare URI strings (from
    ``--instruments`` or the offline fallback) and ``{name, uri}`` dicts
    (from a live browser walk, where ``uri`` is an opaque
    ``query:Drums#FileId_NNNNN``). Strings get ``name == uri``; dicts
    without a ``uri`` are dropped. Giving the planner the human ``name``
    lets it judge kit character even when the URI is opaque.
    """
    out: list[dict[str, str]] = []
    for item in available:
        if isinstance(item, dict):
            uri = item.get("uri")
            if not uri:
                continue
            out.append({"name": str(item.get("name") or uri), "uri": str(uri)})
        elif isinstance(item, str) and item.strip():
            out.append({"name": item, "uri": item})
    return out


def _default_summary(transcription: Transcription) -> dict:
    """Drum transcription summary (the historic shape)."""
    return {
        "tempo_bpm": round(transcription.tempo_bpm, 2),
        "bars": round(transcription.bars, 2),
        "hit_count": len(transcription.hits),
        "hit_histogram": _hit_histogram(transcription),
    }


def _instruments_block(available_instruments: list[dict[str, str]]) -> str:
    """The instrument list as a system block.

    Lives in ``system`` (not the user message) so it sits BEFORE the last
    prompt-cache breakpoint: the kit list is stable across takes in a session
    (it comes from the kit cache), so caching it saves re-processing ~7K
    tokens on every plan call. Compact JSON — pretty-printing burned tokens
    for nothing.
    """
    return (
        "Available instruments — each has a human `name` (use it to judge kit "
        "character) and an opaque `uri`. Choose ONE and return its `uri` "
        "verbatim as instrument_path:\n"
        + json.dumps(available_instruments, separators=(",", ":"), sort_keys=True)
    )


def _user_message(
    transcription: Transcription,
    user_hint: str | None,
    *,
    summary: dict | None = None,
) -> str:
    """The per-take (volatile) part of the request: summary + hint only."""
    if summary is None:
        summary = _default_summary(transcription)
    parts = [
        "Transcription summary:",
        json.dumps(summary, separators=(",", ":"), sort_keys=True),
    ]
    if user_hint:
        parts += ["", f"User hint: {user_hint}"]
    parts += ["", "Emit the plan by calling the emit_plan tool."]
    return "\n".join(parts)


def make_plan(
    transcription: Transcription,
    session_state: dict,
    user_hint: str | None = None,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    device: "DeviceSpec | None" = None,
) -> Plan:
    """Plan a clip for ``transcription``.

    ``device`` (a ``DeviceSpec``) selects the per-voice system prompt and
    transcription summary. When omitted, the drum defaults are used — and a
    drums ``DeviceSpec`` produces a byte-identical request to ``device=None``.
    """
    available = _normalise_instruments(session_state.get("available_instruments", []))
    if not available:
        raise ValueError("session_state['available_instruments'] must be non-empty")
    uris = {a["uri"] for a in available}

    if device is not None:
        prompt_path = device.prompt_path
        summary = {
            "tempo_bpm": round(transcription.tempo_bpm, 2),
            "bars": round(transcription.bars, 2),
            **device.plan_summary(transcription),
        }
    else:
        prompt_path = _PROMPT_PATH
        summary = None  # _user_message builds the drum default

    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; pass client= for tests.")
        client = anthropic.Anthropic(api_key=api_key)

    # Prompt caching (prefix-match: tools -> system -> messages). Two
    # breakpoints: (1) after the static prompt (caches tools + prompt),
    # (2) after the instrument list — the list is stable across takes in a
    # session (kit cache), so repeat takes re-process ONLY the tiny per-take
    # summary. Previously the list sat in the user message AFTER the last
    # breakpoint and its ~7K tokens were re-billed on every single take.
    # Thinking is disabled explicitly: on this model, omitting the field
    # runs adaptive thinking — dead latency for a forced tool call.
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        # The pinned anthropic SDK (0.39.0, held back by the httpx pin) has no
        # typed `thinking` kwarg — a bare thinking= is a TypeError. extra_body
        # serialises straight into the request JSON, which is all we need.
        extra_body={"thinking": {"type": "disabled"}},
        system=[
            {
                "type": "text",
                "text": _system_prompt(prompt_path),
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": _instruments_block(available),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        tools=[_tool_schema()],
        tool_choice={"type": "tool", "name": "emit_plan"},
        messages=[
            {
                "role": "user",
                "content": _user_message(transcription, user_hint, summary=summary),
            }
        ],
    )

    tool_block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
    if tool_block is None:
        raise RuntimeError(f"Claude returned no tool_use block: {response.content}")

    try:
        llm_plan = _LLMPlan.model_validate(tool_block.input)
    except ValidationError as exc:
        raise RuntimeError(f"Plan failed schema validation: {exc}") from exc

    # Enforce instrument existence; fall back to the first available URI if
    # Claude hallucinated one, and note it in rationale.
    rationale = llm_plan.rationale
    fixed_clips: list[ClipPlan] = []
    for clip in llm_plan.clips:
        instrument = clip.instrument_path
        if instrument not in uris:
            instrument = available[0]["uri"]
            rationale = f"[fallback: chosen instrument not in session] {rationale}"
        fixed_clips.append(
            ClipPlan(
                track_name=clip.track_name,
                instrument_path=instrument,
                midi_file=transcription.midi_path,
                length_bars=clip.length_bars,
            )
        )

    return Plan(tempo=llm_plan.tempo, clips=fixed_clips, rationale=rationale)
