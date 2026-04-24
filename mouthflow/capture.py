"""Record audio from the default input or validate an existing WAV.

All output is normalised to 44.1 kHz, 16-bit, mono — the format the rest of
the pipeline assumes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 44_100
CHANNELS = 1
SUBTYPE = "PCM_16"


def record(duration_s: float = 15.0, out_path: Path | None = None) -> Path:
    """Record `duration_s` seconds from the default input device.

    Blocks until the recording completes. Returns the path to the WAV.
    """
    if duration_s <= 0:
        raise ValueError(f"duration_s must be positive, got {duration_s}")

    frames = int(round(duration_s * SAMPLE_RATE))
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()

    if out_path is None:
        out_path = Path(tempfile.mkstemp(suffix=".wav", prefix="mouthflow_")[1])
    out_path = Path(out_path)
    sf.write(out_path, audio, SAMPLE_RATE, subtype=SUBTYPE)
    return out_path


def from_file(path: Path) -> Path:
    """Validate and normalise an existing audio file to 44.1/16/mono WAV.

    If the input already matches the target format, returns the path
    unchanged. Otherwise writes a normalised copy next to the original with
    a `.normalised.wav` suffix and returns that path.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    try:
        info = sf.info(path)
    except sf.LibsndfileError as exc:
        raise ValueError(f"not a readable audio file: {path}") from exc

    if (
        info.samplerate == SAMPLE_RATE
        and info.channels == CHANNELS
        and info.subtype == SUBTYPE
    ):
        return path

    audio, sr = sf.read(path, always_2d=True)
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1, keepdims=True)
    if sr != SAMPLE_RATE:
        audio = _resample(audio[:, 0], sr, SAMPLE_RATE)[:, None]

    out_path = path.with_suffix(".normalised.wav")
    sf.write(out_path, audio, SAMPLE_RATE, subtype=SUBTYPE)
    return out_path


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    import soxr

    return soxr.resample(x, sr_in, sr_out)
