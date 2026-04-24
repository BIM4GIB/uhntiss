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
from typing import Any

import anthropic
from pydantic import BaseModel, Field, ValidationError

from mouthflow.schemas import ClipPlan, Plan, Transcription

DEFAULT_MODEL = "claude-sonnet-4-6"
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


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


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


def _user_message(
    transcription: Transcription,
    available_instruments: list[str],
    user_hint: str | None,
) -> str:
    summary = {
        "tempo_bpm": round(transcription.tempo_bpm, 2),
        "bars": round(transcription.bars, 2),
        "hit_count": len(transcription.hits),
        "hit_histogram": _hit_histogram(transcription),
    }
    parts = [
        "Transcription summary:",
        json.dumps(summary, indent=2),
        "",
        "Available instruments (browser URIs — pick one of these verbatim):",
        json.dumps(available_instruments, indent=2),
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
) -> Plan:
    available = session_state.get("available_instruments", [])
    if not available:
        raise ValueError("session_state['available_instruments'] must be non-empty")

    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; pass client= for tests.")
        client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_system_prompt(),
        tools=[_tool_schema()],
        tool_choice={"type": "tool", "name": "emit_plan"},
        messages=[
            {
                "role": "user",
                "content": _user_message(transcription, available, user_hint),
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
        if instrument not in available:
            instrument = available[0]
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
