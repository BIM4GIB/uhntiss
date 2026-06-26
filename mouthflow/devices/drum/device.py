"""The drums device — the first plugin, re-expressing the original pipeline."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from mouthflow.devices.base import ClipMode, DeviceSpec
from mouthflow.devices.drum.transcriber import DrumTranscriber
from mouthflow.devices.registry import register
from mouthflow.schemas import Intent, Transcription

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "plan.md"

# Used ONLY when Live is unreachable, to keep offline dry-run producing a Plan.
# These synthetic URIs do NOT resolve in a real Live install.
_FALLBACK_INSTRUMENTS = (
    "query:Drums#Kit-Core%20808",
    "query:Drums#Kit-Core%20Jazz",
    "query:Drums#Kit-Core%20Kit",
)

_GM_NAMES = {36: "kick", 38: "snare", 42: "hat_closed", 46: "hat_open", 39: "perc"}


def drum_plan_summary(t: Transcription) -> dict:
    """Voice-specific transcription summary for the planner: a hit histogram.

    The planner adds the common fields (tempo, bars); this contributes the
    drum vocabulary. Mirrors plan._hit_histogram so behaviour is unchanged.
    """
    hist = Counter(_GM_NAMES.get(h.midi_note, str(h.midi_note)) for h in t.hits)
    return {"hit_count": len(t.hits), "hit_histogram": dict(hist)}


DRUM_DEVICE = DeviceSpec(
    id="drums",
    intent=Intent.DRUM,
    transcriber=DrumTranscriber(),
    clip_mode=ClipMode.PERCUSSIVE,
    browser_category="Drums",
    prompt_path=_PROMPT_PATH,
    plan_summary=drum_plan_summary,
    instrument_filter=None,
    fallback_instruments=_FALLBACK_INSTRUMENTS,
)

register(DRUM_DEVICE)
