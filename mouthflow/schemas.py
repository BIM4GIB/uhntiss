"""Pydantic schemas and shared enums."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class Transcription:
    midi_path: Path
    tempo_bpm: float
    bars: float
    hits: list[NoteEvent]


class ClipPlan(BaseModel):
    track_name: str
    instrument_path: str
    midi_file: Path
    length_bars: float


class Plan(BaseModel):
    tempo: float
    clips: list[ClipPlan]
    rationale: str = Field(..., description="1-2 sentences explaining the choice.")
