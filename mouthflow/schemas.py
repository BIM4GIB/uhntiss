"""Pydantic schemas and shared enums."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Intent(str, Enum):
    DRUM = "drum"
    MELODY = "melody"
    BASS = "bass"
    UNKNOWN = "unknown"


@dataclass
class DrumHit:
    time_s: float
    midi_note: int
    velocity: int


@dataclass
class Transcription:
    midi_path: Path
    tempo_bpm: float
    bars: float
    hits: list[DrumHit]


class ClipPlan(BaseModel):
    track_name: str
    instrument_path: str
    midi_file: Path
    length_bars: float


class Plan(BaseModel):
    tempo: float
    clips: list[ClipPlan]
    rationale: str = Field(..., description="1-2 sentences explaining the choice.")
