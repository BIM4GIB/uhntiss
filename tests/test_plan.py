"""Tests for mouthflow.plan.

The anthropic client is stubbed; we exercise the surrounding logic
(schema validation, instrument fallback, user-hint wiring, message
shape) without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mouthflow.plan import (
    _hit_histogram,
    _instruments_block,
    _normalise_instruments,
    _user_message,
    make_plan,
)
from mouthflow.schemas import DrumHit, Transcription


def _transcription(tmp_path: Path) -> Transcription:
    midi = tmp_path / "t.mid"
    midi.write_bytes(b"")  # existence only; plan.py doesn't read it
    return Transcription(
        midi_path=midi,
        tempo_bpm=96.0,
        bars=4.0,
        hits=[
            DrumHit(time_s=0.0, midi_note=36, velocity=110),
            DrumHit(time_s=0.5, midi_note=38, velocity=95),
            DrumHit(time_s=0.75, midi_note=42, velocity=70),
            DrumHit(time_s=1.0, midi_note=36, velocity=110),
        ],
    )


@dataclass
class _FakeToolUse:
    type: str
    input: dict
    id: str = "tool_1"
    name: str = "emit_plan"


class _FakeMessages:
    def __init__(self, tool_input: dict) -> None:
        self.tool_input = tool_input
        self.last_request: dict | None = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return SimpleNamespace(content=[_FakeToolUse(type="tool_use", input=self.tool_input)])


class _FakeClient:
    def __init__(self, tool_input: dict) -> None:
        self.messages = _FakeMessages(tool_input)


def test_hit_histogram_counts_gm_notes(tmp_path):
    t = _transcription(tmp_path)
    assert _hit_histogram(t) == {"kick": 2, "snare": 1, "hat_closed": 1}


def test_normalise_instruments_accepts_strings_and_dicts():
    out = _normalise_instruments(
        [
            "query:Drums#A",
            {"name": "Dusty", "uri": "query:Drums#FileId_1"},
            {"uri": "query:Drums#FileId_2"},  # name defaults to uri
            {"name": "no uri"},  # dropped — no uri
            "",  # dropped — blank
        ]
    )
    assert out == [
        {"name": "query:Drums#A", "uri": "query:Drums#A"},
        {"name": "Dusty", "uri": "query:Drums#FileId_1"},
        {"name": "query:Drums#FileId_2", "uri": "query:Drums#FileId_2"},
    ]


def test_user_message_is_summary_and_hint_only(tmp_path):
    # The user message is the VOLATILE tail of the prompt — summary + hint.
    # Instruments live in a cached system block (see _instruments_block).
    t = _transcription(tmp_path)
    msg = _user_message(t, user_hint="harder")
    assert "harder" in msg
    assert "tempo_bpm" in msg


def test_instruments_block_shows_names_and_uris():
    block = _instruments_block(
        [
            {"name": "808 Kit", "uri": "query:Drums#FileId_1"},
            {"name": "Jazz", "uri": "query:Drums#FileId_2"},
        ]
    )
    assert "808 Kit" in block  # name shown so the planner can judge character
    assert "query:Drums#FileId_1" in block  # uri shown so it can return it


def test_make_plan_round_trips_valid_tool_output(tmp_path):
    t = _transcription(tmp_path)
    instruments = ["query:Drums#Kit-Core%20808", "query:Drums#Kit-Jazz"]
    client = _FakeClient(
        {
            "tempo": 96.0,
            "clips": [
                {
                    "track_name": "Drums",
                    "instrument_path": "query:Drums#Kit-Core%20808",
                    "length_bars": 4.0,
                }
            ],
            "rationale": "Picked an 808 kit; pattern is kick-heavy.",
        }
    )
    plan = make_plan(
        t,
        session_state={"available_instruments": instruments},
        client=client,  # type: ignore[arg-type]
    )
    assert plan.tempo == 96.0
    assert plan.clips[0].instrument_path == "query:Drums#Kit-Core%20808"
    assert plan.clips[0].midi_file == t.midi_path
    assert "808" in plan.rationale

    # Verify the request shape the client saw.
    req = client.messages.last_request
    assert req["model"].startswith("claude-sonnet-5")
    assert req["tool_choice"] == {"type": "tool", "name": "emit_plan"}
    assert req["tools"][0]["name"] == "emit_plan"
    # Thinking is disabled explicitly (adaptive-on-by-omission would add
    # latency to a forced tool call).
    assert req["thinking"] == {"type": "disabled"}
    # System is [static prompt, instrument list], BOTH cache-marked, so a
    # repeat take re-processes only the tiny per-take user message.
    assert isinstance(req["system"], list) and len(req["system"]) == 2
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "Kit-Core%20808" in req["system"][1]["text"]


def test_make_plan_accepts_dict_instruments_and_returns_uri(tmp_path):
    t = _transcription(tmp_path)
    instruments = [
        {"name": "808 Core Kit", "uri": "query:Drums#FileId_5006"},
        {"name": "Dusty Breaks", "uri": "query:Drums#FileId_5012"},
    ]
    client = _FakeClient(
        {
            "tempo": 96.0,
            "clips": [
                {
                    "track_name": "Drums",
                    "instrument_path": "query:Drums#FileId_5012",
                    "length_bars": 4.0,
                }
            ],
            "rationale": "Dusty Breaks fits the laid-back pocket.",
        }
    )
    plan = make_plan(
        t,
        session_state={"available_instruments": instruments},
        client=client,  # type: ignore[arg-type]
    )
    assert plan.clips[0].instrument_path == "query:Drums#FileId_5012"
    assert "fallback" not in plan.rationale.lower()
    # The (cached) instruments system block exposed kit names so the planner
    # could choose by character.
    sent = client.messages.last_request["system"][1]["text"]
    assert "Dusty Breaks" in sent


def test_make_plan_falls_back_when_instrument_hallucinated(tmp_path):
    t = _transcription(tmp_path)
    instruments = ["query:Drums#Kit-Core%20808"]
    client = _FakeClient(
        {
            "tempo": 96.0,
            "clips": [
                {
                    "track_name": "Drums",
                    "instrument_path": "query:Drums#Does-Not-Exist",
                    "length_bars": 4.0,
                }
            ],
            "rationale": "Went with a jazz kit.",
        }
    )
    plan = make_plan(
        t,
        session_state={"available_instruments": instruments},
        client=client,  # type: ignore[arg-type]
    )
    assert plan.clips[0].instrument_path == instruments[0]
    assert "fallback" in plan.rationale.lower()


def test_make_plan_rejects_invalid_schema(tmp_path):
    t = _transcription(tmp_path)
    client = _FakeClient({"tempo": -1, "clips": [], "rationale": ""})
    with pytest.raises(RuntimeError, match="schema validation"):
        make_plan(
            t,
            session_state={"available_instruments": ["x"]},
            client=client,  # type: ignore[arg-type]
        )


def test_make_plan_requires_available_instruments(tmp_path):
    t = _transcription(tmp_path)
    with pytest.raises(ValueError, match="available_instruments"):
        make_plan(t, session_state={"available_instruments": []})
