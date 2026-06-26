"""Pydantic schemas and shared enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Intent(str, Enum):
    DRUM = "drum"
    MELODY = "melody"   # lead synth
    BASS = "bass"
    DRONE = "drone"     # ambient / pad
    UNKNOWN = "unknown"


@dataclass
class NoteEvent:
    """A single note: onset, pitch, velocity, and (optionally) a real duration.

    ``duration_s=None`` means "use the writer's default length" (the drum path's
    fixed 1/32 note); pitched and drone voices set a real sustain.
    """

    time_s: float
    midi_note: int
    velocity: int
    duration_s: float | None = None


# Drums historically used ``DrumHit``; it's just a note event without a duration.
DrumHit = NoteEvent


class AutomationEnvelope(BaseModel):
    """A device-parameter automation curve for a clip, as normalized steps.

    ``steps`` are ``(time_in_beats, value_0_1)``; the bridge scales ``value``
    into the parameter's real range. ``parameter`` is resolved by name on the
    device at ``device_index`` (default: the first rack Macro). Produced by a
    transcriber (e.g. the drone loudness contour), not by the LLM planner.
    """

    parameter: str = "Macro 1"
    device_index: int = 0
    steps: list[tuple[float, float]]


@dataclass
class Transcription:
    midi_path: Path
    tempo_bpm: float
    bars: float
    hits: list[NoteEvent]
    automation: list[AutomationEnvelope] = field(default_factory=list)


class ClipPlan(BaseModel):
    track_name: str
    instrument_path: str
    midi_file: Path
    length_bars: float
    automation: list[AutomationEnvelope] | None = None


class Plan(BaseModel):
    tempo: float
    clips: list[ClipPlan]
    rationale: str = Field(..., description="1-2 sentences explaining the choice.")
