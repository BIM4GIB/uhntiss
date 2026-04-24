"""Beatbox WAV -> drum MIDI + tempo."""

from __future__ import annotations

from pathlib import Path

from mouthflow.schemas import Transcription


def transcribe_drums(wav_path: Path) -> Transcription:
    raise NotImplementedError
