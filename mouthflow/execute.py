"""Apply a Plan to a running Ableton Live session via ableton-mcp's socket."""

from __future__ import annotations

from pathlib import Path

from mouthflow.schemas import Plan


class AbletonClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9877) -> None:
        self.host = host
        self.port = port

    def get_session_info(self) -> dict:
        raise NotImplementedError

    def create_midi_track(self, name: str) -> int:
        raise NotImplementedError

    def load_instrument(self, track_idx: int, path: str) -> None:
        raise NotImplementedError

    def insert_midi_clip(self, track_idx: int, midi_path: Path, bars: float) -> None:
        raise NotImplementedError

    def set_tempo(self, bpm: float) -> None:
        raise NotImplementedError

    def fire_clip(self, track_idx: int, clip_idx: int = 0) -> None:
        raise NotImplementedError


def apply_plan(plan: Plan, client: AbletonClient) -> None:
    raise NotImplementedError
