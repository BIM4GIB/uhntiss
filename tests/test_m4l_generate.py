"""Tests for the Max for Live panel generator (container + patch injection)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_GEN_PATH = _REPO / "m4l" / "generate.py"

_spec = importlib.util.spec_from_file_location("m4l_generate", _GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_container_round_trips_byte_identical():
    raw = (_REPO / "m4l" / "Mouthflow.amxd").read_bytes()
    assert gen._wrap(*gen._split(raw)) == raw


def test_make_panel_injects_device_message_and_retitles():
    _prefix, maxpat = gen.read_maxpat(_REPO / "m4l" / "Mouthflow.amxd")
    n_boxes = len(maxpat["patcher"]["boxes"])

    gen.make_panel(maxpat, "bass", "MOUTHFLOW · BASS")

    boxes = maxpat["patcher"]["boxes"]
    lines = maxpat["patcher"]["lines"]
    assert len(boxes) == n_boxes + 1
    msg = [b["box"] for b in boxes if b["box"].get("id") == gen._DEVICE_MSG_ID]
    assert msg and msg[0]["text"] == "device bass"
    title = [b["box"]["text"] for b in boxes if b["box"].get("id") == gen._TITLE_ID]
    assert title == ["MOUTHFLOW · BASS"]
    # loadbang -> device message -> node.script
    assert {"patchline": {"source": [gen._LOADBANG_ID, 0], "destination": [gen._DEVICE_MSG_ID, 0]}} in lines
    assert {"patchline": {"source": [gen._DEVICE_MSG_ID, 0], "destination": [gen._NODE_ID, 0]}} in lines


def test_make_panel_is_idempotent():
    _prefix, maxpat = gen.read_maxpat(_REPO / "m4l" / "Mouthflow.amxd")
    gen.make_panel(maxpat, "bass", "T1")
    n = len(maxpat["patcher"]["boxes"])
    gen.make_panel(maxpat, "lead", "T2")  # re-patch same dict
    assert len(maxpat["patcher"]["boxes"]) == n  # no duplicate box
    msg = [b["box"]["text"] for b in maxpat["patcher"]["boxes"] if b["box"].get("id") == gen._DEVICE_MSG_ID]
    assert msg == ["device lead"]  # text updated in place
