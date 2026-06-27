"""Device abstraction for the umbrella product.

Every voice-driven instrument (drums, bass, lead, drone) is a ``DeviceSpec``:
a transcriber strategy plus the data that parameterizes the otherwise-shared
``capture -> classify -> transcribe -> plan -> execute`` pipeline. The two
behaviours that actually differ between voices are the ``Transcriber`` (WAV ->
notes) and the ``plan_summary`` (how the transcription is described to the
planner); everything else is configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from mouthflow.schemas import Intent, Transcription


class ClipMode(str, Enum):
    """How a transcription becomes MIDI/clip events.

    The single switch that captures most transcription/MIDI divergence: drums
    quantize to 16ths and write fixed 1/32 notes on GM channel 9; pitched
    voices write real note durations on channel 0; drone writes long held
    notes (and, later, automation) with no grid snap.
    """

    PERCUSSIVE = "percussive"   # quantize 16ths, 1/32 notes, GM ch9   (drums)
    MONOPHONIC = "monophonic"   # pitched note on/off, real durs, ch0   (bass/lead)
    SUSTAINED = "sustained"     # long held notes / chord, ch0          (drone)


@runtime_checkable
class Transcriber(Protocol):
    """WAV -> ``Transcription``. The one behaviour each device must supply.

    ``tempo`` is an optional forced BPM (from ``--tempo``). The drum device
    honours it; pitched/drone voices accept it for interface uniformity.
    """

    def transcribe(self, wav: Path, *, tempo: float | None = None) -> Transcription: ...


@dataclass(frozen=True)
class DeviceSpec:
    id: str                                   # "drums" | "bass" | "lead" | "drone"
    intent: Intent                            # routing key from classify()
    transcriber: Transcriber
    clip_mode: ClipMode
    browser_category: str                     # Live browser root, e.g. "Drums"
    prompt_path: Path                         # planner system prompt for this voice
    plan_summary: Callable[[Transcription], dict]   # generalizes _hit_histogram
    instrument_filter: Callable[[str], bool] | None = None  # name-keyword filter
    fallback_instruments: tuple[str, ...] = ()              # offline dry-run only
