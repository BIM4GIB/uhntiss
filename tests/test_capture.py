"""Tests for mouthflow.capture."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from mouthflow import capture


def _write_wav(path: Path, duration_s: float, sr: int, channels: int, subtype: str) -> None:
    frames = int(duration_s * sr)
    data = np.zeros((frames, channels), dtype=np.float32)
    sf.write(path, data, sr, subtype=subtype)


def test_record_produces_target_format(tmp_path, monkeypatch):
    """`record` writes a WAV at 44.1 kHz / 16-bit / mono of the right length."""
    captured = {}

    def fake_rec(frames, samplerate, channels, dtype, device=None):
        captured["frames"] = frames
        captured["samplerate"] = samplerate
        captured["channels"] = channels
        captured["device"] = device
        return np.zeros((frames, channels), dtype=np.int16)

    monkeypatch.setattr(capture.sd, "rec", fake_rec)
    monkeypatch.setattr(capture.sd, "wait", lambda: None)

    out = capture.record(2.0, out_path=tmp_path / "out.wav")

    assert out.exists()
    info = sf.info(out)
    assert info.samplerate == 44_100
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    # 2 seconds at 44.1 kHz
    assert info.frames == 88_200
    assert captured["samplerate"] == 44_100
    assert captured["channels"] == 1


def test_record_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        capture.record(0)


def test_record_until_stop_captures_streamed_blocks(tmp_path, monkeypatch):
    """`record_until_stop` records streamed blocks until should_stop, then
    drains the queue — the device's start/stop path."""
    bs = 4_410  # 100 ms blocks

    class FakeStream:
        def __init__(self, *, callback, blocksize, **_):
            self._cb, self._bs = callback, blocksize

        def __enter__(self):
            for _ in range(3):  # three blocks arrive while "recording"
                self._cb(np.ones((self._bs, 1), dtype=np.int16), self._bs, None, None)
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(capture.sd, "InputStream", FakeStream)

    calls = {"n": 0}

    def should_stop():  # stop after the first loop read; the rest is drained
        calls["n"] += 1
        return calls["n"] > 1

    out = capture.record_until_stop(should_stop, out_path=tmp_path / "s.wav", blocksize=bs)
    info = sf.info(out)
    assert info.samplerate == 44_100 and info.channels == 1 and info.subtype == "PCM_16"
    assert info.frames == 3 * bs  # all three streamed blocks captured


def test_from_file_passthrough_when_already_target(tmp_path):
    path = tmp_path / "ok.wav"
    _write_wav(path, 1.0, 44_100, 1, "PCM_16")
    assert capture.from_file(path) == path


def test_from_file_resamples_stereo_48k(tmp_path):
    src = tmp_path / "stereo.wav"
    _write_wav(src, 1.0, 48_000, 2, "PCM_24")
    out = capture.from_file(src)
    assert out != src
    assert out.name.endswith(".normalised.wav")
    info = sf.info(out)
    assert info.samplerate == 44_100
    assert info.channels == 1
    assert info.subtype == "PCM_16"


def test_from_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        capture.from_file(tmp_path / "nope.wav")


def test_from_file_rejects_non_audio(tmp_path):
    junk = tmp_path / "not.wav"
    junk.write_bytes(b"this is not a wav file")
    with pytest.raises(ValueError):
        capture.from_file(junk)
