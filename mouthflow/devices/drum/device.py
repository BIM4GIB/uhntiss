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

from mouthflow.devices.drum.classify import note_names as _note_names


def drum_plan_summary(t: Transcription) -> dict:
    """Voice-specific transcription summary for the planner: a hit histogram.

    The planner adds the common fields (tempo, bars); this contributes the
    drum vocabulary. Mirrors plan._hit_histogram so behaviour is unchanged.
    """
    names = _note_names()  # GM defaults + the performer's own sound names
    hist = Counter(names.get(h.midi_note, str(h.midi_note)) for h in t.hits)
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
