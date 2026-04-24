"""Intent classification for a captured clip.

v0.1 is hardcoded to DRUM. A real heuristic (onset density > 3/s + low pitch
stability) lands in v0.2.
"""

from __future__ import annotations

from pathlib import Path

from mouthflow.schemas import Intent


def classify(wav_path: Path) -> tuple[Intent, float]:
    # TODO(v0.2): onset-density + pitch-stability heuristic.
    return (Intent.DRUM, 1.0)
