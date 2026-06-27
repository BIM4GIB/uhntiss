"""Tests for the Max for Live panel generator (container + node.script swap).

Per-voice panels bake the device into a per-voice glue file (``mouthflow_<voice>.js``)
that the panel's ``node.script`` loads directly — not via a loadbang message,
which races Node-for-Max startup and gets dropped.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_GEN_PATH = _REPO / "m4l" / "generate.py"

_spec = importlib.util.spec_from_file_location("m4l_generate", _GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_container_round_trips_byte_identical():
    raw = (_REPO / "m4l" / "Mouthflow.amxd").read_bytes()
    assert gen._wrap(*gen._split(raw)) == raw


def test_make_panel_points_node_script_at_voice_glue_and_retitles():
    _prefix, maxpat = gen.read_maxpat(_REPO / "m4l" / "Mouthflow.amxd")
    n_boxes = len(maxpat["patcher"]["boxes"])

    gen.make_panel(maxpat, "mouthflow_bass.js", "MOUTHFLOW · BASS")

    # No boxes added — it's an in-place edit of the existing node.script.
    assert len(maxpat["patcher"]["boxes"]) == n_boxes
    node = gen._node_script_box(maxpat)
    assert node["text"] == "node.script mouthflow_bass.js @autostart 1"
    assert node["textfile"]["filename"] == "mouthflow_bass.js"
    title = [b["box"]["text"] for b in maxpat["patcher"]["boxes"] if b["box"].get("id") == gen._TITLE_ID]
    assert title == ["MOUTHFLOW · BASS"]


def test_generated_panels_reference_their_voice_glue():
    # The committed panels + glue must stay in sync with the generator.
    for voice, amxd in [("bass", "MouthflowBass.amxd"), ("lead", "MouthflowLead.amxd"),
                        ("drone", "MouthflowDrone.amxd")]:
        js_name = f"mouthflow_{voice}.js"
        _prefix, maxpat = gen.read_maxpat(_REPO / "m4l" / amxd)
        assert gen._node_script_box(maxpat)["text"] == f"node.script {js_name} @autostart 1"

        glue = (_REPO / "m4l" / js_name).read_text(encoding="utf-8")
        default = re.search(r'device:\s*"([^"]*)"', glue).group(1)
        assert default == voice, f"{js_name} device default is {default!r}, expected {voice!r}"
