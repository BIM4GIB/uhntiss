"""Generate per-voice Max for Live panels from the template device.

The ``.amxd`` is an ``ampf`` container wrapping a single ``ptch`` chunk of
maxpat JSON:

    "ampf" u32(4) "aaaa" "meta" u32(4) u32(1) "ptch" u32(json_len) json_bytes

(``json_bytes`` is UTF-8 maxpat + a trailing NUL, both counted in ``json_len``;
``ptch`` is the last chunk). We only ever rewrite the ``ptch`` length + payload,
so the container is reproduced exactly (verified by an identity round-trip).

A per-voice panel is the template with two tiny changes: its ``node.script``
points at a per-voice glue file (``mouthflow_<voice>.js``, a copy of
``mouthflow.js`` whose ``device`` default is that voice), and the header is
retitled. **The voice is baked into the JS default — NOT sent as a loadbang
message** — because Node for Max starts asynchronously and a loadbang message
races (and usually loses) the script's startup, leaving the device on its
default. Baking it in the JS the panel loads removes the race entirely.

NOTE: the container handling is verified here; smoke-test the panels in Live
once. The committed ``Mouthflow.amxd`` (+ ``mouthflow.js``) is the proven drums
panel and is left untouched.

Usage::

    python m4l/generate.py            # regenerate bass/lead/drone panels + glue
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "Mouthflow.amxd"
_GLUE = _HERE / "mouthflow.js"
_PTCH = b"ptch"

# Live loads M4L devices from here; node.script resolves the glue next to the
# .amxd, so the panels AND their .js must both live in this folder.
_USER_DEVICES = Path.home() / "Music" / "Ableton" / "User Library" / "Devices"

_TITLE_ID = "obj-100"  # the "MOUTHFLOW" header comment


# --- container (de)serialization ---

def _split(data: bytes) -> tuple[bytes, bytes]:
    """Return (prefix-through-'ptch'-tag, payload-bytes-incl-trailing-NUL)."""
    i = data.find(_PTCH)
    if i < 0:
        raise ValueError("not an ampf/ptch .amxd: no ptch chunk")
    length = struct.unpack("<I", data[i + 4 : i + 8])[0]
    return data[: i + 4], data[i + 8 : i + 8 + length]


def _wrap(prefix: bytes, payload: bytes) -> bytes:
    return prefix + struct.pack("<I", len(payload)) + payload


def read_maxpat(path: Path) -> tuple[bytes, dict]:
    prefix, payload = _split(Path(path).read_bytes())
    return prefix, json.loads(payload.rstrip(b"\x00").decode("utf-8"))


def write_amxd(path: Path, prefix: bytes, maxpat: dict) -> None:
    payload = json.dumps(maxpat, indent=1).encode("utf-8") + b"\x00"
    Path(path).write_bytes(_wrap(prefix, payload))


# --- per-voice glue ---

def write_voice_glue(voice: str) -> str:
    """Write ``mouthflow_<voice>.js`` (a copy of the glue with ``device``
    defaulted to ``voice``) and return its filename."""
    text = _GLUE.read_text(encoding="utf-8")
    patched, n = re.subn(r'device:\s*"[^"]*"', f'device: "{voice}"', text, count=1)
    if n != 1:
        raise RuntimeError("could not find the `device:` default in mouthflow.js")
    name = f"mouthflow_{voice}.js"
    (_HERE / name).write_text(patched, encoding="utf-8")
    return name


# --- panel construction ---

def _node_script_box(maxpat: dict) -> dict:
    for b in maxpat["patcher"]["boxes"]:
        box = b["box"]
        if box.get("maxclass") == "newobj" and str(box.get("text", "")).startswith("node.script"):
            return box
    raise RuntimeError("no node.script object found in the patch")


def make_panel(maxpat: dict, js_filename: str, title: str) -> dict:
    """Point the panel's node.script at ``js_filename`` and retitle the header."""
    node = _node_script_box(maxpat)
    node["text"] = f"node.script {js_filename} @autostart 1"
    if isinstance(node.get("textfile"), dict):
        node["textfile"]["filename"] = js_filename

    for b in maxpat["patcher"]["boxes"]:
        if b["box"].get("id") == _TITLE_ID:
            b["box"]["text"] = title
    return maxpat


_NODE_INLET = "obj-4"  # node.script object; all controls feed its inlet 0


def _inject_controls(maxpat: dict, pitched: bool = True) -> None:
    """Append the in-device controls and wire them to the node.script inlet.

    All are clones of the template's proven UI pattern (a UI object -> an
    optional ``prepend <handler>`` -> the node.script inlet), so the glue's
    existing message handlers (transcribe_clip / record_start / record_stop /
    bars / correct / key / scale) receive them. New ids start at obj-200 to
    avoid colliding with the template (max id 153).

    ``pitched=False`` (the drums panel) keeps the voice-neutral controls —
    Transcribe Clip, record start/stop, the level meter — and skips the
    note-correction / bar-fit fields, which only the pitched refine stage
    reads.
    """
    boxes = maxpat["patcher"]["boxes"]
    lines = maxpat["patcher"].setdefault("lines", [])
    add_b: list[dict] = []
    add_l: list[dict] = []

    def box(**kw):
        add_b.append({"box": kw})

    def msg(bid, text, p, pr=None):
        kw = dict(id=bid, maxclass="message", numinlets=2, numoutlets=1,
                  outlettype=[""], text=text, patching_rect=p, fontsize=12.0)
        if pr is not None:
            kw.update(presentation=1, presentation_rect=pr)
        box(**kw)

    def comment(bid, text, p, pr):
        box(id=bid, maxclass="comment", numinlets=1, numoutlets=0, text=text,
            patching_rect=p, presentation=1, presentation_rect=pr)

    def textedit(bid, p, pr):
        box(id=bid, maxclass="textedit", numinlets=1, numoutlets=2, outlettype=["", ""],
            parameter_enable=0, keymode=1, patching_rect=p, presentation=1, presentation_rect=pr)

    def toggle(bid, p, pr):
        box(id=bid, maxclass="toggle", numinlets=1, numoutlets=1, outlettype=["int"],
            parameter_enable=0, patching_rect=p, presentation=1, presentation_rect=pr)

    def newobj(bid, text, p):
        box(id=bid, maxclass="newobj", numinlets=1, numoutlets=1, outlettype=[""],
            text=text, patching_rect=p)

    def wire(src, dst, so=0, di=0):
        add_l.append({"patchline": {"source": [src, so], "destination": [dst, di]}})

    # Presentation layout: Live's device strip is FIXED-height (~196px, the
    # template's "Device vertical limit") but grows horizontally, so the new
    # controls go in a SECOND COLUMN to the right (x>=330), within the same
    # vertical band as the originals — NOT stacked below (which Live clips).
    # Action buttons -> node inlet (literal messages the glue handles directly).
    msg("obj-200", "transcribe_clip", [720, 100, 140, 20], [330, 36, 152, 22])
    msg("obj-201", "record_start", [720, 130, 110, 20], [330, 64, 100, 22])
    msg("obj-202", "record_stop", [840, 130, 110, 20], [436, 64, 100, 22])
    for b in ("obj-200", "obj-201", "obj-202"):
        wire(b, _NODE_INLET)

    if pitched:
        # bars: textedit -> prepend bars -> node
        comment("obj-212", "bars(1-16)", [860, 170, 150, 18], [330, 96, 60, 16])
        textedit("obj-210", [720, 170, 120, 22], [392, 94, 44, 22])
        newobj("obj-211", "prepend bars", [720, 200, 110, 22])
        wire("obj-210", "obj-211"); wire("obj-211", _NODE_INLET)

        # correct: toggle (defaulted ON via loadbang) -> prepend correct -> node
        comment("obj-222", "correct", [880, 240, 80, 18], [444, 96, 46, 16])
        toggle("obj-220", [720, 240, 24, 24], [492, 94, 20, 20])
        newobj("obj-221", "prepend correct", [760, 240, 120, 22])
        newobj("obj-223", "loadbang", [920, 240, 60, 22])
        msg("obj-224", "1", [920, 272, 32, 20])
        wire("obj-220", "obj-221"); wire("obj-221", _NODE_INLET)
        wire("obj-223", "obj-224"); wire("obj-224", "obj-220")

        # key: textedit -> prepend key -> node
        comment("obj-232", "key", [860, 290, 60, 18], [330, 124, 24, 16])
        textedit("obj-230", [720, 290, 120, 22], [356, 122, 46, 22])
        newobj("obj-231", "prepend key", [720, 320, 100, 22])
        wire("obj-230", "obj-231"); wire("obj-231", _NODE_INLET)

        # scale: textedit -> prepend scale -> node
        comment("obj-242", "scale", [860, 350, 60, 18], [410, 124, 34, 16])
        textedit("obj-240", [720, 350, 120, 22], [446, 122, 90, 22])
        newobj("obj-241", "prepend scale", [720, 380, 100, 22])
        wire("obj-240", "obj-241"); wire("obj-241", _NODE_INLET)

    # input level meter: node.script outlet 0 -> route level -> flonum (dBFS
    # while record-streaming). Parallel to the template's own
    # `route status kitmenu` off the same outlet.
    newobj("obj-250", "route level", [720, 420, 100, 22])
    box(id="obj-251", maxclass="flonum", numinlets=1, numoutlets=2,
        outlettype=["", "bang"], parameter_enable=0,
        patching_rect=[720, 450, 50, 22], presentation=1,
        presentation_rect=[488, 36, 48, 22])
    wire(_NODE_INLET, "obj-250")  # from node.script outlet 0
    wire("obj-250", "obj-251")

    boxes.extend(add_b)
    lines.extend(add_l)


def _grow_device(maxpat: dict, width: float = 600.0, height: float = 240.0) -> None:
    """Widen the device so the second control column is in view.

    Live's device strip is fixed-height, so the fix is horizontal room, not
    vertical — bump the patcher width to fit the right-hand column.
    """
    pat = maxpat["patcher"]
    r = pat.get("rect", [236.0, 105.0, 385.0, 229.0])
    pat["rect"] = [r[0], r[1], float(width), float(height)]
    o = pat.get("openrect", [0.0, 0.0, 0.0, 169.0])
    pat["openrect"] = [o[0], o[1], o[2], float(height)]


def _validate(maxpat: dict) -> None:
    """Every box id is unique and every patchline endpoint exists."""
    ids = [b["box"]["id"] for b in maxpat["patcher"]["boxes"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RuntimeError(f"duplicate box ids: {sorted(dupes)}")
    idset = set(ids)
    for ln in maxpat["patcher"].get("lines", []):
        for end in ("source", "destination"):
            ref = ln["patchline"][end][0]
            if ref not in idset:
                raise RuntimeError(f"patchline {end} references missing box {ref}")


def generate_panel(voice: str, out_name: str, title: str, pitched: bool = True) -> tuple[Path, str]:
    js_name = write_voice_glue(voice)
    prefix, maxpat = read_maxpat(_TEMPLATE)
    make_panel(maxpat, js_name, title)
    _inject_controls(maxpat, pitched=pitched)
    _grow_device(maxpat)  # make room for the second control column
    _validate(maxpat)
    out = _HERE / out_name
    write_amxd(out, prefix, maxpat)
    return out, js_name


def _self_check() -> None:
    """The container must round-trip byte-for-byte for an identity rewrite."""
    raw = _TEMPLATE.read_bytes()
    assert _wrap(*_split(raw)) == raw, "container round-trip is not byte-identical"
    print("ok   container round-trip is byte-identical")


# (voice, output panel, header title, pitched controls?)
# MouthflowDrums supersedes the bare template Mouthflow.amxd (still installed
# for compatibility): same drums voice, plus Transcribe Clip, the start/stop
# record buttons, and the input level meter.
_PANELS = [
    ("drums", "MouthflowDrums.amxd", "MOUTHFLOW · DRUMS", False),
    ("bass", "MouthflowBass.amxd", "MOUTHFLOW · BASS", True),
    ("lead", "MouthflowLead.amxd", "MOUTHFLOW · LEAD", True),
    ("drone", "MouthflowDrone.amxd", "MOUTHFLOW · DRONE", True),
]


def install_to_user_library(dest: Path = _USER_DEVICES) -> None:
    """Copy the panels + glue into Live's User Library/Devices (with .bak backups).

    Keeps the device Live actually loads in sync with the repo — otherwise a
    regenerated panel here looks 'the same' in Live, which loads the stale copy.
    Both the .amxd and its node.script .js must land here (node.script resolves
    the glue next to the .amxd)."""
    if not dest.is_dir():
        raise SystemExit(f"User Library devices folder not found: {dest}")
    names = ["Mouthflow.amxd", "mouthflow.js", "package.json"]
    names += [out for _, out, _, _ in _PANELS]
    names += [f"mouthflow_{voice}.js" for voice, _, _, _ in _PANELS]
    for name in names:
        src = _HERE / name
        if not src.exists():
            continue
        target = dest / name
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        shutil.copy2(src, target)
        print(f"ok   installed {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the per-voice M4L panels.")
    parser.add_argument(
        "--install", action="store_true",
        help="Also copy panels + glue into Live's User Library/Devices (with .bak backups).",
    )
    args = parser.parse_args()

    _self_check()
    for voice, out_name, title, pitched in _PANELS:
        out, js_name = generate_panel(voice, out_name, title, pitched=pitched)
        print(f"ok   wrote {out.name}  ->  node.script {js_name}  (device {voice})")
    if args.install:
        install_to_user_library()


if __name__ == "__main__":
    main()
