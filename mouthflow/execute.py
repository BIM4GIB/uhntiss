"""Apply a Plan to a running Ableton Live session via ableton-mcp's socket.

Protocol (from ahujasid/ableton-mcp MCP_Server/server.py):

- TCP :9877, JSON per message.
- Request: ``{"type": <command>, "params": {...}}``
- Response: ``{"status": "ok"|"error", "result": {...}, "message": "..."}``

A message is complete when the accumulated bytes parse as JSON.

The ``AbletonClient`` wraps the raw protocol. ``apply_plan`` is the only
caller of interest: it reads the MIDI file emitted by ``transcribe.py``,
creates a track per clip in the plan, loads the chosen instrument, inserts
the notes, sets the tempo, and fires clip 0.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any, Callable

import mido

from mouthflow.schemas import Plan

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
_RECV_CHUNK = 8192
_TIMEOUT_READ = 10.0
_TIMEOUT_WRITE = 15.0


class AbletonError(RuntimeError):
    """Ableton returned status=error or the socket misbehaved."""


class AbletonClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None

    # --- connection ---

    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.host, self.port))
        self._sock = s

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "AbletonClient":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- raw protocol ---

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict:
        self.connect()
        assert self._sock is not None
        payload = json.dumps({"type": command_type, "params": params or {}}).encode("utf-8")
        self._sock.settimeout(_TIMEOUT_WRITE)
        self._sock.sendall(payload)

        self._sock.settimeout(_TIMEOUT_READ)
        chunks: list[bytes] = []
        while True:
            chunk = self._sock.recv(_RECV_CHUNK)
            if not chunk:
                if not chunks:
                    raise AbletonError("connection closed before any data")
                break
            chunks.append(chunk)
            try:
                response = json.loads(b"".join(chunks).decode("utf-8"))
                break
            except json.JSONDecodeError:
                continue
        else:  # pragma: no cover
            raise AbletonError("no data")

        if response.get("status") == "error":
            raise AbletonError(response.get("message", "unknown Ableton error"))
        return response.get("result", {})

    # --- high-level ---

    def get_session_info(self) -> dict:
        return self.send_command("get_session_info")

    def get_browser_items_at_path(self, path: str) -> dict:
        """Raw ableton-mcp browser listing at ``path`` (e.g. ``"Drums"``).

        Returns the command's ``result`` dict: ``{path, name, uri,
        is_folder, is_loadable, items: [...]}`` where each item is
        ``{name, is_folder, is_device, is_loadable, uri}``.
        """
        return self.send_command("get_browser_items_at_path", {"path": path})

    def list_instruments(
        self,
        category: str = "Instruments",
        *,
        max_depth: int = 2,
        hard_cap: int = 1000,
        name_filter: "Callable[[str], bool] | None" = None,
    ) -> list[dict[str, str]]:
        """Discover loadable instruments under a browser ``category`` as
        ``[{name, uri}]``.

        Walks ableton-mcp's browser under ``category`` (e.g. ``"Drums"``,
        ``"Instruments"``, ``"Sounds"``). A category mixes directly-loadable
        devices/presets with folders, so we descend folders up to
        ``max_depth`` and keep only ``is_loadable`` leaves carrying a real
        URI. Those URIs (``query:<Cat>#FileId_NNNNN``) are what
        ``load_browser_item`` actually accepts — unlike the synthetic
        fallback URIs, which do not resolve in a real install.

        ``name_filter`` (a device's ``instrument_filter``) keeps only leaves
        whose human name passes — e.g. pad/ambient presets for the drone
        device. Returns the *full* matching set; ``hard_cap`` is a runaway
        guard. Callers that need to bound a prompt should sample the result
        rather than relying on traversal order (alphabetical → early-letter
        bias).
        """
        found: list[dict[str, str]] = []
        seen: set[str] = set()

        def walk(p: str, depth: int) -> None:
            if len(found) >= hard_cap:
                return
            try:
                result = self.get_browser_items_at_path(p)
            except AbletonError:
                return  # unreachable subtree; skip it
            folders: list[str] = []
            for item in result.get("items", []):
                uri = item.get("uri")
                if item.get("is_loadable") and uri and uri not in seen:
                    name = str(item.get("name") or uri)
                    if name_filter is not None and not name_filter(name):
                        continue
                    seen.add(uri)
                    found.append({"name": name, "uri": str(uri)})
                    if len(found) >= hard_cap:
                        return
                elif item.get("is_folder") and item.get("name"):
                    folders.append(item["name"])
            if depth < max_depth:
                for name in folders:
                    walk("{0}/{1}".format(p, name), depth + 1)
                    if len(found) >= hard_cap:
                        return

        walk(category, 0)
        return found

    def list_drum_instruments(
        self,
        path: str = "Drums",
        *,
        max_depth: int = 2,
        hard_cap: int = 1000,
    ) -> list[dict[str, str]]:
        """Back-compat shim: ``list_instruments`` over the Drums category."""
        return self.list_instruments(path, max_depth=max_depth, hard_cap=hard_cap)

    def create_midi_track(self, name: str, index: int = -1) -> int:
        result = self.send_command("create_midi_track", {"index": index})
        track_idx = int(result.get("index", result.get("track_index", -1)))
        if track_idx < 0:
            raise AbletonError(f"create_midi_track returned no index: {result}")
        self.send_command("set_track_name", {"track_index": track_idx, "name": name})
        return track_idx

    def load_instrument(self, track_idx: int, uri: str) -> None:
        # ``uri`` is the browser URI the Planner was handed in session_state.
        # In v0.1 we pass it straight through to ableton-mcp.
        self.send_command("load_browser_item", {"track_index": track_idx, "item_uri": uri})

    def insert_midi_clip(self, track_idx: int, midi_path: Path, bars: float) -> None:
        length_beats = float(bars) * 4.0  # assume 4/4; Planner's tempo is what matters
        self.send_command(
            "create_clip",
            {"track_index": track_idx, "clip_index": 0, "length": length_beats},
        )
        notes = _midi_to_notes(midi_path)
        self.send_command(
            "add_notes_to_clip",
            {"track_index": track_idx, "clip_index": 0, "notes": notes},
        )

    def set_tempo(self, bpm: float) -> None:
        self.send_command("set_tempo", {"tempo": float(bpm)})

    def fire_clip(self, track_idx: int, clip_idx: int = 0) -> None:
        self.send_command("fire_clip", {"track_index": track_idx, "clip_index": clip_idx})

    def set_clip_envelope(
        self,
        track_idx: int,
        clip_idx: int,
        device_index: int,
        parameter: str,
        steps: list[tuple[float, float]],
    ) -> None:
        """Write a clip automation envelope for a device parameter.

        Requires the forked ableton-mcp Remote Script that adds the
        ``set_clip_envelope`` command (see ``bridge/``). ``steps`` are
        ``(time_in_beats, value_0_1)``; the bridge scales value into the
        parameter's real range. Raises ``AbletonError`` if the command is
        unknown (stock bridge) — ``apply_plan`` treats that as "no automation".
        """
        self.send_command(
            "set_clip_envelope",
            {
                "track_index": track_idx,
                "clip_index": clip_idx,
                "device_index": device_index,
                "parameter": parameter,
                "steps": [[float(t), float(v)] for t, v in steps],
            },
        )


def _midi_to_notes(midi_path: Path) -> list[dict]:
    """Flatten a MIDI file to ableton-mcp's note dict format.

    Each note: ``{pitch, start_time (beats), duration (beats), velocity, mute}``.
    """
    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat
    notes: list[dict] = []
    for track in mid.tracks:
        abs_tick = 0
        pending: dict[int, tuple[int, int]] = {}  # pitch -> (start_tick, velocity)
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                pending[msg.note] = (abs_tick, msg.velocity)
            elif msg.type in ("note_off",) or (msg.type == "note_on" and msg.velocity == 0):
                start = pending.pop(msg.note, None)
                if start is None:
                    continue
                start_tick, velocity = start
                notes.append(
                    {
                        "pitch": int(msg.note),
                        "start_time": start_tick / tpb,
                        "duration": max((abs_tick - start_tick) / tpb, 1 / 16),
                        "velocity": int(velocity),
                        "mute": False,
                    }
                )
    notes.sort(key=lambda n: (n["start_time"], n["pitch"]))
    return notes


def apply_plan(plan: Plan, client: AbletonClient) -> None:
    """Apply ``plan`` to a running Live session via ``client``."""
    client.set_tempo(plan.tempo)
    for clip in plan.clips:
        track_idx = client.create_midi_track(clip.track_name)
        client.load_instrument(track_idx, clip.instrument_path)
        client.insert_midi_clip(track_idx, clip.midi_file, clip.length_bars)
        for env in clip.automation or []:
            try:
                client.set_clip_envelope(track_idx, 0, env.device_index, env.parameter, env.steps)
            except AbletonError as exc:
                # Stock bridge lacks set_clip_envelope; the drone still plays as
                # a held note/chord. Degrade gracefully rather than abort.
                print(f"[mouthflow] automation skipped ({exc})", file=sys.stderr)
        client.fire_clip(track_idx, 0)
