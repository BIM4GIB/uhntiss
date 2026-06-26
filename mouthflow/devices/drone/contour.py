"""Contour extraction for the drone device.

Turns the performance's loudness shape into a normalized automation curve that
drives a device macro (e.g. a filter/timbre macro on the chosen pad). This is
the "contour -> automation" layer: the *note* is the held pitch; the *movement*
is this curve mapped onto a macro. Writing it into Live needs the forked bridge
command ``set_clip_envelope`` (see ``bridge/``); without it the drone still
plays as a held note/chord.
"""

from __future__ import annotations

import numpy as np

from mouthflow.schemas import AutomationEnvelope

_HOP = 512


def extract_loudness_contour(
    y: np.ndarray,
    sr: int,
    tempo_bpm: float,
    *,
    n_steps: int = 32,
    parameter: str = "Macro 1",
    device_index: int = 0,
    smooth_win: int = 9,
) -> AutomationEnvelope:
    """Loudness (RMS) envelope -> normalized ``AutomationEnvelope`` in beats.

    The clip's RMS is smoothed, min-max normalized to [0, 1], then sampled to
    ``n_steps`` breakpoints across the clip, with time expressed in beats so it
    lands on the same timeline as the MIDI clip.
    """
    rms = _moving_average(_rms(y), smooth_win)

    lo, hi = float(rms.min()), float(rms.max())
    norm = (rms - lo) / (hi - lo) if hi > lo else np.zeros_like(rms)

    dur_s = len(y) / sr
    total_beats = dur_s * tempo_bpm / 60.0

    steps: list[tuple[float, float]] = []
    last = max(1, len(norm) - 1)
    denom = max(1, n_steps - 1)
    for k in range(n_steps):
        frac = k / denom
        t_beat = round(frac * total_beats, 4)
        value = round(float(norm[int(round(frac * last))]), 4)
        steps.append((t_beat, value))

    return AutomationEnvelope(parameter=parameter, device_index=device_index, steps=steps)


def _rms(y: np.ndarray) -> np.ndarray:
    import librosa

    return librosa.feature.rms(y=y, frame_length=2048, hop_length=_HOP)[0]


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or x.size <= win:
        return x
    kernel = np.ones(win, dtype=float) / win
    return np.convolve(x, kernel, mode="same")
