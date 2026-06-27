"""The bass device — hum a bassline, get a low monophonic MIDI line.

Reuses the shared ``PitchedTranscriber`` with a bass ``VoiceConfig``: a low
search range and an E1–E3 octave-snap target (so a line hummed an octave high
lands in the bass register).
"""

from __future__ import annotations

from pathlib import Path

from mouthflow.devices.base import ClipMode, DeviceSpec
from mouthflow.devices.pitched import PitchedTranscriber, VoiceConfig, pitched_plan_summary
from mouthflow.devices.registry import register
from mouthflow.schemas import Intent

_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.md"

# Synthetic fallback URIs — keep offline dry-run producing a Plan; they do NOT
# resolve in a real Live install (the planner picks a real preset when Live is
# reachable). The real category below was confirmed against Live 12.3's browser:
# sounds/Bass holds 515 loadable bass presets (query:Sounds#Bass:FileId_NNNNN).
_FALLBACK_INSTRUMENTS = (
    "query:Sounds#Bass:Bass",
    "query:Sounds#Bass:Sub",
)

BASS_CONFIG = VoiceConfig(
    fmin=40.0,
    fmax=400.0,
    target_lo=28,   # E1
    target_hi=52,   # E3
    division=8,     # bass tends to sit on 1/8 grid
    frame_length=4096,  # large window for reliable low-f0 estimation
    min_note_s=0.08,
    merge_gap_s=0.10,
)

BASS_DEVICE = DeviceSpec(
    id="bass",
    intent=Intent.BASS,
    transcriber=PitchedTranscriber(BASS_CONFIG),
    clip_mode=ClipMode.MONOPHONIC,
    browser_category="sounds/Bass",
    prompt_path=_PROMPT_PATH,
    plan_summary=pitched_plan_summary,
    instrument_filter=None,
    fallback_instruments=_FALLBACK_INSTRUMENTS,
)

register(BASS_DEVICE)
