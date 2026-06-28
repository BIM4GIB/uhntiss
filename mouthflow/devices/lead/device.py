"""The lead device — sing a melodic line, get a monophonic lead-synth MIDI part.

A config clone of the bass device on the same ``PitchedTranscriber``: a wider,
higher search range, a higher octave-snap target, a finer quantize grid, and
legato-friendly gap bridging.
"""

from __future__ import annotations

from pathlib import Path

from mouthflow.devices.base import ClipMode, DeviceSpec
from mouthflow.devices.pitched import PitchedTranscriber, VoiceConfig, pitched_plan_summary
from mouthflow.devices.registry import register
from mouthflow.schemas import Intent

_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.md"

# Real category confirmed against Live 12.3: sounds/Synth Lead holds 342
# loadable lead presets (query:Sounds#Synth%20Lead:FileId_NNNNN). Fallbacks are
# synthetic and only keep offline dry-run producing a Plan.
_FALLBACK_INSTRUMENTS = (
    "query:Sounds#Synth Lead:Lead",
    "query:Sounds#Synth Lead:Pluck",
)

LEAD_CONFIG = VoiceConfig(
    fmin=110.0,
    fmax=1200.0,
    target_lo=55,   # G3
    target_hi=84,   # C6
    division=16,    # leads sit on a finer grid
    frame_length=2048,
    min_note_s=0.06,
    merge_gap_s=0.12,  # bridge breaths for legato phrasing
)

# Lead maps to Intent.MELODY (the enum's name for a melodic lead voice).
LEAD_DEVICE = DeviceSpec(
    id="lead",
    intent=Intent.MELODY,
    transcriber=PitchedTranscriber(LEAD_CONFIG),
    clip_mode=ClipMode.MONOPHONIC,
    browser_category="sounds/Synth Lead",
    prompt_path=_PROMPT_PATH,
    plan_summary=pitched_plan_summary,
    instrument_filter=None,
    fallback_instruments=_FALLBACK_INSTRUMENTS,
)

register(LEAD_DEVICE)
