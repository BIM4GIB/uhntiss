"""The drone / ambient-pad device.

Voice a sustained tone (or hum a sequence of tones) → a long held note / chord
on a pad. The planner picks a pad/ambient/texture instrument. The pitch +
loudness contour is additionally rendered as device automation when the forked
bridge command is available (see ``contour.py`` and ``apply_plan``).
"""

from __future__ import annotations

from pathlib import Path

from mouthflow.devices.base import ClipMode, DeviceSpec
from mouthflow.devices.drone.transcriber import DroneTranscriber, drone_plan_summary
from mouthflow.devices.registry import register
from mouthflow.schemas import Intent

_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.md"

# Real category confirmed against Live 12.3: sounds/Ambient & Evolving holds
# hundreds of loadable pad/texture/drone presets
# (query:Sounds#Ambient%20&%20Evolving:FileId_NNNNN). Fallbacks are synthetic.
_FALLBACK_INSTRUMENTS = (
    "query:Sounds#Ambient & Evolving:Pad",
    "query:Sounds#Ambient & Evolving:Drone",
)

DRONE_DEVICE = DeviceSpec(
    id="drone",
    intent=Intent.DRONE,
    transcriber=DroneTranscriber(),
    clip_mode=ClipMode.SUSTAINED,
    browser_category="sounds/Ambient & Evolving",
    prompt_path=_PROMPT_PATH,
    plan_summary=drone_plan_summary,
    instrument_filter=None,
    fallback_instruments=_FALLBACK_INSTRUMENTS,
)

register(DRONE_DEVICE)
