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

import json
import re
import struct
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "Mouthflow.amxd"
_GLUE = _HERE / "mouthflow.js"
_PTCH = b"ptch"

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


def generate_panel(voice: str, out_name: str, title: str) -> tuple[Path, str]:
    js_name = write_voice_glue(voice)
    prefix, maxpat = read_maxpat(_TEMPLATE)
    make_panel(maxpat, js_name, title)
    out = _HERE / out_name
    write_amxd(out, prefix, maxpat)
    return out, js_name


def _self_check() -> None:
    """The container must round-trip byte-for-byte for an identity rewrite."""
    raw = _TEMPLATE.read_bytes()
    assert _wrap(*_split(raw)) == raw, "container round-trip is not byte-identical"
    print("ok   container round-trip is byte-identical")


_PANELS = [
    ("bass", "MouthflowBass.amxd", "MOUTHFLOW · BASS"),
    ("lead", "MouthflowLead.amxd", "MOUTHFLOW · LEAD"),
    ("drone", "MouthflowDrone.amxd", "MOUTHFLOW · DRONE"),
]


def main() -> None:
    _self_check()
    for voice, out_name, title in _PANELS:
        out, js_name = generate_panel(voice, out_name, title)
        print(f"ok   wrote {out.name}  ->  node.script {js_name}  (device {voice})")


if __name__ == "__main__":
    main()
