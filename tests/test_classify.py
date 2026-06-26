"""Tests for the intent router (classify)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from mouthflow.classify import classify
from mouthflow.schemas import Intent

SR = 44_100


def _write(path: Path, y: np.ndarray) -> Path:
    sf.write(path, y, SR, subtype="PCM_16")
    return path


def _sine(freq: float, dur_s: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * dur_s)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _kick(dur_s: float = 0.12) -> np.ndarray:
    t = np.arange(int(SR * dur_s)) / SR
    return (0.8 * np.exp(-t * 40) * np.sin(2 * np.pi * 60 * t)).astype(np.float32)


def test_routes_percussive_to_drums(tmp_path):
    # Sparse low-frequency hits with mostly silence -> mostly unvoiced -> drums.
    y = np.zeros(int(2.0 * SR), dtype=np.float32)
    k = _kick()
    for n in range(4):
        s = int(n * 0.5 * SR)
        y[s : s + len(k)] += k
    intent, _conf = classify(_write(tmp_path / "drum.wav", y))
    assert intent == Intent.DRUM


def test_routes_sustained_tone_to_drone(tmp_path):
    # A steady held pitch -> drone.
    intent, _conf = classify(_write(tmp_path / "drone.wav", _sine(196.0, 2.0)))
    assert intent == Intent.DRONE


def test_routes_low_moving_line_to_bass(tmp_path):
    # Voiced, moving pitch, low register -> bass.
    y = np.concatenate([_sine(98.0, 0.5), _sine(110.0, 0.5), _sine(82.41, 0.5), _sine(110.0, 0.5)])
    intent, _conf = classify(_write(tmp_path / "bass.wav", y))
    assert intent == Intent.BASS


def test_routes_high_moving_line_to_lead(tmp_path):
    # Voiced, moving pitch, high register -> lead (Intent.MELODY).
    y = np.concatenate([_sine(523.25, 0.4), _sine(587.33, 0.4), _sine(659.25, 0.4), _sine(587.33, 0.4)])
    intent, _conf = classify(_write(tmp_path / "lead.wav", y))
    assert intent == Intent.MELODY
