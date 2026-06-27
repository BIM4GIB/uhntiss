"""Generate per-voice Max for Live panels from the template device.

The ``.amxd`` is an ``ampf`` container wrapping a single ``ptch`` chunk of
maxpat JSON:

    "ampf" u32(4) "aaaa" "meta" u32(4) u32(1) "ptch" u32(json_len) json_bytes

(``json_bytes`` is UTF-8 maxpat + a trailing NUL, both counted in ``json_len``;
``ptch`` is the last chunk). We only ever rewrite the ``ptch`` length + payload,
so the container is reproduced exactly (verified by an identity round-trip).

A per-voice panel is the template with one addition: a ``loadbang``-driven
``device <id>`` message wired into ``node.script`` (mirroring the existing
``repo``/``uv`` config messages), plus a retitled header. All panels share the
one ``mouthflow.js`` glue, which forwards ``--device <id>`` to the CLI.

NOTE: the container handling is verified here, but the generated panels should
get a ~30s smoke test in Live (drag on a track, click Generate) before relying
on them — this script can't open Max. The committed ``Mouthflow.amxd`` remains
the proven drums panel.

Usage::

    python m4l/generate.py            # regenerate bass/lead/drone panels + self-check
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "Mouthflow.amxd"
_PTCH = b"ptch"

_NODE_ID = "obj-4"        # node.script mouthflow.js
_LOADBANG_ID = "obj-140"  # loadbang that seeds defaults
_TITLE_ID = "obj-100"     # the "MOUTHFLOW" header comment
_DEVICE_MSG_ID = "obj-300"  # our injected message box (above the existing ids)


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


# --- panel construction ---

def make_panel(maxpat: dict, device_id: str, title: str) -> dict:
    """Inject a loadbang-driven ``device <id>`` message and retitle the header."""
    patcher = maxpat["patcher"]
    boxes = patcher["boxes"]
    lines = patcher.setdefault("lines", [])

    # Don't double-inject if regenerating from an already-patched file.
    if not any(b["box"].get("id") == _DEVICE_MSG_ID for b in boxes):
        boxes.append(
            {
                "box": {
                    "id": _DEVICE_MSG_ID,
                    "maxclass": "message",
                    "numinlets": 2,
                    "numoutlets": 1,
                    "outlettype": [""],
                    "text": f"device {device_id}",
                    "patching_rect": [720.0, 235.0, 100.0, 20.0],
                    "fontsize": 12.0,
                }
            }
        )
        lines.append({"patchline": {"source": [_LOADBANG_ID, 0], "destination": [_DEVICE_MSG_ID, 0]}})
        lines.append({"patchline": {"source": [_DEVICE_MSG_ID, 0], "destination": [_NODE_ID, 0]}})
    else:
        for b in boxes:
            if b["box"].get("id") == _DEVICE_MSG_ID:
                b["box"]["text"] = f"device {device_id}"

    for b in boxes:
        if b["box"].get("id") == _TITLE_ID:
            b["box"]["text"] = title
    return maxpat


def generate_panel(device_id: str, out_name: str, title: str) -> Path:
    prefix, maxpat = read_maxpat(_TEMPLATE)
    make_panel(maxpat, device_id, title)
    out = _HERE / out_name
    write_amxd(out, prefix, maxpat)
    return out


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
    for device_id, out_name, title in _PANELS:
        out = generate_panel(device_id, out_name, title)
        print(f"ok   wrote {out.name}  (device {device_id})")


if __name__ == "__main__":
    main()
