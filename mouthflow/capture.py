"""Record audio from the default input or validate an existing WAV.

All output is normalised to 44.1 kHz, 16-bit, mono — the format the rest of
the pipeline assumes.

Recordings land in the take vault (``~/.mouthflow/takes/``) rather than a
tempdir: a performance must survive whatever fails after it (Ableton down,
API error, crash), so it can be replayed with ``mouthflow retry-last``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 44_100
CHANNELS = 1
SUBTYPE = "PCM_16"

# The take vault. Module-level so tests (and callers) can repoint it.
TAKES_DIR = Path.home() / ".mouthflow" / "takes"


def _take_path() -> Path:
    """A fresh timestamped WAV path in the take vault."""
    TAKES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = TAKES_DIR / f"take-{stamp}.wav"
    i = 1
    while path.exists():  # same-second collision
        path = TAKES_DIR / f"take-{stamp}-{i}.wav"
        i += 1
    return path


def record(
    duration_s: float = 15.0,
    out_path: Path | None = None,
    input_device: int | None = None,
) -> Path:
    """Record `duration_s` seconds from an input device (default if None).

    Blocks until the recording completes. Returns the path to the WAV.
    """
    if duration_s <= 0:
        raise ValueError(f"duration_s must be positive, got {duration_s}")

    frames = int(round(duration_s * SAMPLE_RATE))
    audio = sd.rec(
        frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", device=input_device
    )
    sd.wait()

    out_path = Path(out_path) if out_path is not None else _take_path()
    sf.write(out_path, audio, SAMPLE_RATE, subtype=SUBTYPE)
    return out_path


def record_until_stop(
    should_stop,
    out_path: Path | None = None,
    input_device: int | None = None,
    max_s: float = 600.0,
    blocksize: int | None = None,
    on_level=None,
) -> Path:
    """Record from the mic until ``should_stop()`` is truthy (or ``max_s``).

    The open-ended counterpart to ``record`` — the device's start/stop button
    drives it (the CLI's ``record-stream`` polls stdin for ``should_stop``).
    Captures via a streaming callback into a queue so the duration is the
    performer's, not a fixed timer. ``on_level`` (if given) receives the
    running input level in dBFS a few times a second, for a live meter.
    Returns the WAV path.
    """
    import queue

    out_path = Path(out_path) if out_path is not None else _take_path()
    bs = blocksize or SAMPLE_RATE // 10  # 100 ms blocks
    q: queue.Queue = queue.Queue()

    def _cb(indata, frames, time_info, status):  # noqa: ARG001 — sd callback signature
        q.put(indata.copy())

    chunks: list[np.ndarray] = []
    elapsed = 0.0
    last_level_s = -1.0
    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
        device=input_device, blocksize=bs, callback=_cb,
    ):
        while not should_stop() and elapsed < max_s:
            try:
                chunk = q.get(timeout=0.1)
            except queue.Empty:
                continue
            chunks.append(chunk)
            elapsed += len(chunk) / SAMPLE_RATE
            if on_level is not None and elapsed - last_level_s >= 0.25:
                last_level_s = elapsed
                x = chunk.astype(np.float64) / 32768.0
                rms = float(np.sqrt(np.mean(x**2)))
                on_level(20 * np.log10(max(rms, 1e-6)))
    while not q.empty():  # drain anything captured after the stop signal
        chunks.append(q.get_nowait())

    audio = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, CHANNELS), dtype="int16")
    sf.write(out_path, audio, SAMPLE_RATE, subtype=SUBTYPE)
    return out_path


def list_input_devices() -> list[dict]:
    """Input-capable audio devices as ``[{index, name}]`` (for a UI picker)."""
    out: list[dict] = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append({"index": i, "name": str(d.get("name", f"device {i}"))})
    return out


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
