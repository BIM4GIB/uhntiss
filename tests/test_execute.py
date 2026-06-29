"""Tests for mouthflow.execute.

Uses a fake socket server (thread + loopback TCP) to exercise the real
``AbletonClient`` end-to-end without mocking internals.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import mido
import pytest

from mouthflow.execute import AbletonClient, AbletonError, _midi_to_notes, apply_plan
from mouthflow.schemas import AutomationEnvelope, ClipPlan, Plan


class FakeAbleton:
    """Minimal loopback TCP server that records requests and replies scripted."""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self.requests: list[dict] = []
        self.responses = list(responses) if responses is not None else []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._stop = threading.Event()

    def __enter__(self) -> "FakeAbleton":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass

    def _serve(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(8192)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while True:
                    try:
                        req = json.loads(buf.decode("utf-8"))
                    except json.JSONDecodeError:
                        break
                    self.requests.append(req)
                    buf = b""
                    resp = self.responses.pop(0) if self.responses else {"status": "ok", "result": {}}
                    conn.sendall(json.dumps(resp).encode("utf-8"))


def test_send_command_roundtrip():
    with FakeAbleton([{"status": "ok", "result": {"tempo": 120.0}}]) as fake:
        with AbletonClient(port=fake.port) as client:
            result = client.get_session_info()
    assert result == {"tempo": 120.0}
    assert fake.requests == [{"type": "get_session_info", "params": {}}]


def test_send_command_raises_on_error():
    with FakeAbleton([{"status": "error", "message": "no such track"}]) as fake:
        with AbletonClient(port=fake.port) as client:
            with pytest.raises(AbletonError, match="no such track"):
                client.send_command("get_track_info", {"track_index": 99})


def test_list_drum_instruments_filters_loadable_and_recurses():
    # Top-level "Drums" mixes a loadable kit, a folder, and a non-loadable
    # leaf; the folder holds another loadable kit one level down.
    drums = {
        "status": "ok",
        "result": {
            "path": "Drums",
            "items": [
                {"name": "808 Core Kit", "is_folder": False, "is_loadable": True,
                 "uri": "query:Drums#FileId_5006"},
                {"name": "Kits", "is_folder": True, "is_loadable": False, "uri": None},
                {"name": "Header", "is_folder": False, "is_loadable": False,
                 "uri": "query:Drums#FileId_9999"},
            ],
        },
    }
    folder = {
        "status": "ok",
        "result": {
            "path": "Drums/Kits",
            "items": [
                {"name": "Dusty Breaks", "is_folder": False, "is_loadable": True,
                 "uri": "query:Drums#FileId_5012"},
            ],
        },
    }
    with FakeAbleton([drums, folder]) as fake:
        with AbletonClient(port=fake.port) as client:
            kits = client.list_drum_instruments()
    assert kits == [
        {"name": "808 Core Kit", "uri": "query:Drums#FileId_5006"},
        {"name": "Dusty Breaks", "uri": "query:Drums#FileId_5012"},
    ]
    assert fake.requests[0] == {"type": "get_browser_items_at_path", "params": {"path": "Drums"}}
    assert fake.requests[1]["params"]["path"] == "Drums/Kits"


def test_list_drum_instruments_empty_when_no_loadable():
    resp = {"status": "ok", "result": {"path": "Drums", "error": "Unknown category", "items": []}}
    with FakeAbleton([resp]) as fake:
        with AbletonClient(port=fake.port) as client:
            assert client.list_drum_instruments() == []


def test_get_selected_clip():
    info = {"name": "Audio 1", "file_path": "/tmp/a.aif", "is_audio": True}
    with FakeAbleton([{"status": "ok", "result": info}]) as fake:
        with AbletonClient(port=fake.port) as client:
            got = client.get_selected_clip()
    assert got == info
    assert fake.requests[0] == {"type": "get_selected_clip", "params": {}}


def test_create_midi_track_sets_name():
    responses = [
        {"status": "ok", "result": {"index": 3}},  # create_midi_track
        {"status": "ok", "result": {}},            # set_track_name
    ]
    with FakeAbleton(responses) as fake:
        with AbletonClient(port=fake.port) as client:
            idx = client.create_midi_track("Drums")
    assert idx == 3
    assert fake.requests[0]["type"] == "create_midi_track"
    assert fake.requests[1] == {
        "type": "set_track_name",
        "params": {"track_index": 3, "name": "Drums"},
    }


def _write_simple_midi(path: Path) -> None:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    # Kick on beat 1, snare on beat 2
    track.append(mido.Message("note_on", note=36, velocity=100, time=0, channel=9))
    track.append(mido.Message("note_off", note=36, velocity=0, time=240, channel=9))
    track.append(mido.Message("note_on", note=38, velocity=90, time=240, channel=9))
    track.append(mido.Message("note_off", note=38, velocity=0, time=240, channel=9))
    mid.save(path)


def test_midi_to_notes_extracts_pitches_and_times(tmp_path):
    midi = tmp_path / "test.mid"
    _write_simple_midi(midi)
    notes = _midi_to_notes(midi)
    assert len(notes) == 2
    assert notes[0]["pitch"] == 36
    assert notes[0]["start_time"] == pytest.approx(0.0)
    assert notes[0]["duration"] == pytest.approx(0.5)
    assert notes[1]["pitch"] == 38
    assert notes[1]["start_time"] == pytest.approx(1.0)


def test_apply_plan_sends_expected_commands(tmp_path):
    midi = tmp_path / "clip.mid"
    _write_simple_midi(midi)
    plan = Plan(
        tempo=92.0,
        clips=[
            ClipPlan(
                track_name="Drums",
                instrument_path="query:Drums#Kit-Core%20808",
                midi_file=midi,
                length_bars=2.0,
            )
        ],
        rationale="test",
    )
    responses = [
        {"status": "ok", "result": {}},                 # set_tempo
        {"status": "ok", "result": {"index": 0}},       # create_midi_track
        {"status": "ok", "result": {}},                 # set_track_name
        {"status": "ok", "result": {}},                 # load_browser_item
        {"status": "ok", "result": {}},                 # create_clip
        {"status": "ok", "result": {}},                 # add_notes_to_clip
        {"status": "ok", "result": {}},                 # fire_clip
    ]
    with FakeAbleton(responses) as fake:
        with AbletonClient(port=fake.port) as client:
            apply_plan(plan, client)

    types = [r["type"] for r in fake.requests]
    assert types == [
        "set_tempo",
        "create_midi_track",
        "set_track_name",
        "load_browser_item",
        "create_clip",
        "add_notes_to_clip",
        "fire_clip",
    ]
    # set_tempo received the right BPM
    assert fake.requests[0]["params"] == {"tempo": 92.0}
    # create_clip length is bars * 4
    assert fake.requests[4]["params"]["length"] == pytest.approx(8.0)
    # notes made it through
    assert len(fake.requests[5]["params"]["notes"]) == 2


def test_set_clip_envelope_serializes_steps():
    with FakeAbleton([{"status": "ok", "result": {"parameter": "Macro 1", "steps": 2}}]) as fake:
        with AbletonClient(port=fake.port) as client:
            client.set_clip_envelope(2, 0, 0, "Macro 1", [(0.0, 0.0), (4.0, 1.0)])
    req = fake.requests[0]
    assert req["type"] == "set_clip_envelope"
    assert req["params"]["track_index"] == 2
    assert req["params"]["parameter"] == "Macro 1"
    assert req["params"]["steps"] == [[0.0, 0.0], [4.0, 1.0]]


def _drone_plan(midi: Path) -> Plan:
    return Plan(
        tempo=90.0,
        clips=[
            ClipPlan(
                track_name="Drone",
                instrument_path="query:Instruments#Pad",
                midi_file=midi,
                length_bars=4.0,
                automation=[AutomationEnvelope(parameter="Macro 1", steps=[(0.0, 0.0), (4.0, 1.0)])],
            )
        ],
        rationale="pad",
    )


def test_apply_plan_writes_automation_when_supported(tmp_path):
    midi = tmp_path / "d.mid"
    _write_simple_midi(midi)
    responses = [{"status": "ok", "result": {"index": 0}} if i == 1 else {"status": "ok", "result": {}}
                 for i in range(8)]  # set_tempo, create_track, name, load, clip, notes, envelope, fire
    with FakeAbleton(responses) as fake:
        with AbletonClient(port=fake.port) as client:
            apply_plan(_drone_plan(midi), client)
    types = [r["type"] for r in fake.requests]
    assert types == [
        "set_tempo", "create_midi_track", "set_track_name", "load_browser_item",
        "create_clip", "add_notes_to_clip", "set_clip_envelope", "fire_clip",
    ]


def test_apply_plan_degrades_when_envelope_unsupported(tmp_path):
    # Stock bridge: set_clip_envelope replies error. apply_plan must NOT abort
    # and must still fire the clip (drone degrades to a held note/chord).
    midi = tmp_path / "d.mid"
    _write_simple_midi(midi)
    responses = [
        {"status": "ok", "result": {}},               # set_tempo
        {"status": "ok", "result": {"index": 0}},      # create_midi_track
        {"status": "ok", "result": {}},                # set_track_name
        {"status": "ok", "result": {}},                # load_browser_item
        {"status": "ok", "result": {}},                # create_clip
        {"status": "ok", "result": {}},                # add_notes_to_clip
        {"status": "error", "message": "Unknown command type: set_clip_envelope"},
        {"status": "ok", "result": {}},                # fire_clip
    ]
    with FakeAbleton(responses) as fake:
        with AbletonClient(port=fake.port) as client:
            apply_plan(_drone_plan(midi), client)  # must not raise
    assert fake.requests[-1]["type"] == "fire_clip"
