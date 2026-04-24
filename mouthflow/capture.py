"""Record audio from the default input or validate an existing WAV."""

from __future__ import annotations

from pathlib import Path


def record(duration_s: float = 15.0, out_path: Path | None = None) -> Path:
    raise NotImplementedError


def from_file(path: Path) -> Path:
    raise NotImplementedError
