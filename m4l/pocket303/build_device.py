"""Build `Pocket 303.amxd` (Max for Live instrument) + `pocket303.gendsp` from pocket303.genexpr.

The .amxd is an `ampf` container, the same layout as the Mouthflow devices in m4l/ but with
the `iiii` (instrument) tag:  "ampf" u32(4) "iiii" "meta" u32(4) u32(1) "ptch" u32(len) <maxpat json>\\0

    python m4l/pocket303/build_device.py             # writes build/
    python m4l/pocket303/build_device.py --install   # ...and copies the device + js into
                                                     #    ~/Music/Ableton/User Library/Devices/Pocket 303/

Device patch:  midiin -> midiparse -> js pocket303_notes.js (mono note logic) -> unpack -> gen~ (5 ins)
               live.dial x8 (0..100 %) -> / 100. -> prepend <param> -> gen~ ; live.menu Wave -> prepend square
               umenu Sound -> sel -> preset lists -> unpack -> the dials      gen~ out 1/2 -> plugout~
"""
import argparse, datetime, json, pathlib, shutil, struct, subprocess

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"
USER_DEVICES = pathlib.Path.home() / "Music/Ableton/User Library/Devices/Pocket 303"
JS = "pocket303_notes.js"

# (gen param, dial label, min, max, initial, unitstyle, converter)   unitstyle: 5 = %, 7 = semitones
PARAMS = [
    ("cutoff",    "Cutoff",   0,   100, 30,  5, "/ 100."),
    ("resonance", "Reso",     0,   100, 79,  5, "/ 100."),
    ("envmod",    "Env Mod",  0,   100, 67,  5, "/ 100."),
    ("decay",     "Decay",    0,   100, 20,  5, "/ 100."),
    ("accent",    "Accent",   0,   100, 79,  5, "/ 100."),
    ("tuning",    "Tune",   -12,    12,  0,  7, "expr ($f1+12.)/24."),
    ("glide",     "Slide",    0,   100, 30,  5, "/ 100."),
    ("volume",    "Volume",   0,   100, 80,  5, "/ 100."),
]
# Sound presets = the dial values (same order as PARAMS). Measured against the records, see README.
PRESETS = [
    ("Acid Trojan",        [30, 79, 67, 20, 79, 0, 30, 80]),
    ("Insurgency lasers",  [30, 93, 71, 25, 87, 0, 30, 80]),
    ("Insurgency sweep",   [16, 90, 20, 79, 55, 0, 30, 80]),   # automate Cutoff 16 % -> 55 % over the breakdown
]
IN_NAMES = ["note", "held", "accent", "slide", "trig"]
OUT_NAMES = ["left", "right", "gate_led"]

GEN_META = {
    "fileversion": 1, "appversion": {"major": 9, "minor": 0, "revision": 10, "architecture": "x64", "modernui": 1},
    "classnamespace": "dsp.gen", "rect": [100.0, 100.0, 900.0, 700.0], "bglocked": 0, "openinpresentation": 0,
    "default_fontsize": 12.0, "default_fontface": 0, "default_fontname": "Arial", "gridonopen": 1,
    "gridsize": [15.0, 15.0], "gridsnaponopen": 1, "objectsnaponopen": 1, "statusbarvisible": 2, "toolbarvisible": 1,
    "lefttoolbarpinned": 0, "toptoolbarpinned": 0, "righttoolbarpinned": 0, "bottomtoolbarpinned": 0,
    "toolbars_unpinned_last_save": 0, "tallnewobj": 0, "boxanimatetime": 200, "enablehscroll": 1, "enablevscroll": 1,
    "devicewidth": 0.0, "description": "", "digest": "", "tags": "", "style": "", "subpatcher_template": "",
    "assistshowspatchername": 0,
}


def version_stamp() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    except Exception:
        sha = ""
    return f"{datetime.date.today().isoformat()} {sha}".strip()


def gen_patcher(code: str) -> dict:
    boxes, lines = [], []
    for i, n in enumerate(IN_NAMES):
        boxes.append({"box": {"id": f"obj-in{i+1}", "maxclass": "newobj", "numinlets": 0, "numoutlets": 1,
                              "outlettype": [""], "patching_rect": [30.0 + 90 * i, 30.0, 80.0, 22.0],
                              "text": f"in {i+1} @comment {n}"}})
        lines.append({"patchline": {"source": [f"obj-in{i+1}", 0], "destination": ["obj-code", i]}})
    boxes.append({"box": {"id": "obj-code", "maxclass": "codebox", "code": code, "fontface": 0, "fontname": "Menlo",
                          "fontsize": 12.0, "numinlets": len(IN_NAMES), "numoutlets": len(OUT_NAMES),
                          "outlettype": [""] * len(OUT_NAMES), "patching_rect": [30.0, 80.0, 820.0, 540.0], "style": ""}})
    for j, n in enumerate(OUT_NAMES):
        boxes.append({"box": {"id": f"obj-out{j+1}", "maxclass": "newobj", "numinlets": 1, "numoutlets": 0,
                              "patching_rect": [30.0 + 120 * j, 650.0, 110.0, 22.0], "text": f"out {j+1} @comment {n}"}})
        lines.append({"patchline": {"source": ["obj-code", j], "destination": [f"obj-out{j+1}", 0]}})
    return {"patcher": dict(GEN_META, boxes=boxes, lines=lines)}


def device_patcher(gen: dict, version: str) -> dict:
    boxes, lines = [], []

    def box(bid, **kw):
        kw["id"] = bid
        boxes.append({"box": kw})

    def wire(src, dst, so=0, di=0):
        lines.append({"patchline": {"source": [src, so], "destination": [dst, di]}})

    def newobj(bid, text, rect, nin, nout, outtypes, **extra):
        box(bid, maxclass="newobj", text=text, patching_rect=rect, numinlets=nin, numoutlets=nout,
            outlettype=outtypes, **extra)

    # --- MIDI path ---
    newobj("obj-midiin", "midiin", [30.0, 30.0, 50.0, 22.0], 1, 1, ["int"])
    newobj("obj-parse", "midiparse", [30.0, 70.0, 92.5, 22.0], 1, 8, ["", "", "", "int", "int", "", "int", ""])
    newobj("obj-js", f"js {JS}", [30.0, 110.0, 140.0, 22.0], 1, 1, [""],
           saved_object_attributes={"filename": JS, "parameter_enable": 0})
    newobj("obj-unpack", "unpack f f f f f", [30.0, 150.0, 120.0, 22.0], 1, 5, ["float"] * 5)
    wire("obj-midiin", "obj-parse"); wire("obj-parse", "obj-js"); wire("obj-js", "obj-unpack")
    for i in range(5):
        wire("obj-unpack", "obj-gen", i, i)

    # --- the core ---
    box("obj-gen", maxclass="newobj", text="gen~", patching_rect=[30.0, 330.0, 200.0, 22.0], numinlets=5, numoutlets=3,
        outlettype=["signal", "signal", "signal"], varname="pocket303",
        saved_object_attributes={"exportfolder": "", "exportname": "pocket303"}, patcher=gen["patcher"])
    newobj("obj-plugout", "plugout~", [30.0, 400.0, 60.0, 22.0], 2, 2, ["signal", "signal"])
    wire("obj-gen", "obj-plugout", 0, 0); wire("obj-gen", "obj-plugout", 1, 1)

    # --- panel: one row inside Live's fixed-height device strip ---
    params = {}
    box("obj-title", maxclass="comment", text="POCKET 303", fontsize=14.0, fontname="Arial Bold",
        patching_rect=[400.0, 20.0, 120.0, 22.0], numinlets=1, numoutlets=0, presentation=1,
        presentation_rect=[10.0, 6.0, 120.0, 22.0])
    box("obj-stamp", maxclass="comment", text=f"gen~ core · {version}", fontsize=9.0,
        patching_rect=[400.0, 44.0, 220.0, 18.0], numinlets=1, numoutlets=0, presentation=1,
        presentation_rect=[130.0, 10.0, 220.0, 16.0], textcolor=[0.6, 0.6, 0.6, 1.0])
    x0 = 10.0
    for i, (gname, longn, mn, mx, init, unit, conv) in enumerate(PARAMS):
        did, cid, pid = f"obj-dial-{gname}", f"obj-conv-{gname}", f"obj-prep-{gname}"
        px = 400.0 + 95.0 * i
        box(did, maxclass="live.dial", varname=gname, parameter_enable=1, numinlets=1, numoutlets=2,
            outlettype=["", "float"], patching_rect=[px, 80.0, 44.0, 68.0], presentation=1,
            presentation_rect=[x0 + 52.0 * i, 36.0, 44.0, 68.0], appearance=0,
            saved_attribute_attributes={"valueof": {
                "parameter_longname": longn, "parameter_shortname": longn, "parameter_type": 0,
                "parameter_mmin": mn, "parameter_mmax": mx, "parameter_initial": [init],
                "parameter_initial_enable": 1, "parameter_unitstyle": unit}})
        params[did] = [longn, longn, 0]
        newobj(cid, conv, [px, 170.0, 90.0, 22.0], 2 if conv.startswith("/") else 1, 1, [""])
        newobj(pid, f"prepend {gname}", [px, 210.0, 90.0, 22.0], 1, 1, [""])
        wire(did, cid); wire(cid, pid); wire(pid, "obj-gen")
    # wave switch (a Live parameter)
    wx = x0 + 52.0 * 8 + 6.0
    box("obj-menu-square", maxclass="live.menu", varname="square", parameter_enable=1, numinlets=1, numoutlets=3,
        outlettype=["", "", "float"], items=["Saw", "Square"], patching_rect=[400.0 + 95.0 * 8, 80.0, 70.0, 20.0],
        presentation=1, presentation_rect=[wx, 36.0, 90.0, 20.0],
        saved_attribute_attributes={"valueof": {
            "parameter_longname": "Wave", "parameter_shortname": "Wave", "parameter_type": 2,
            "parameter_mmin": 0, "parameter_mmax": 1, "parameter_initial": [0], "parameter_initial_enable": 1,
            "parameter_enum": ["Saw", "Square"]}})
    params["obj-menu-square"] = ["Wave", "Wave", 0]
    newobj("obj-prep-square", "prepend square", [400.0 + 95.0 * 8, 210.0, 100.0, 22.0], 1, 1, [""])
    wire("obj-menu-square", "obj-prep-square"); wire("obj-prep-square", "obj-gen")
    # Sound presets: a plain umenu (not a Live parameter, so it never fires on load) that writes the dials
    items = ["Sound…"] + [name for name, _ in PRESETS]
    box("obj-sound", maxclass="umenu", items=items, numinlets=1, numoutlets=3, outlettype=["int", "", ""],
        patching_rect=[400.0 + 95.0 * 8, 260.0, 120.0, 20.0], presentation=1, presentation_rect=[wx, 66.0, 90.0, 20.0])
    box("obj-sound-label", maxclass="comment", text="presets write the dials", fontsize=9.0,
        patching_rect=[400.0 + 95.0 * 8, 290.0, 130.0, 18.0], numinlets=1, numoutlets=0, presentation=1,
        presentation_rect=[wx, 88.0, 110.0, 16.0], textcolor=[0.6, 0.6, 0.6, 1.0])
    newobj("obj-sound-sel", "sel " + " ".join(str(i + 1) for i in range(len(PRESETS))),
           [540.0 + 95.0 * 8, 260.0, 90.0, 22.0], 1, len(PRESETS) + 1, ["bang"] * len(PRESETS) + [""])
    wire("obj-sound", "obj-sound-sel")
    newobj("obj-sound-unpack", "unpack " + " ".join(["f"] * len(PARAMS)), [540.0 + 95.0 * 8, 360.0, 200.0, 22.0],
           1, len(PARAMS), ["float"] * len(PARAMS))
    for i, (name, values) in enumerate(PRESETS):
        mid = f"obj-sound-msg{i}"
        box(mid, maxclass="message", text=" ".join(str(v) for v in values), numinlets=2, numoutlets=1,
            outlettype=[""], patching_rect=[540.0 + 95.0 * 8 + 110.0 * i, 310.0, 100.0, 22.0])
        wire("obj-sound-sel", mid, i, 0); wire(mid, "obj-sound-unpack")
    for i, (gname, *_rest) in enumerate(PARAMS):
        wire("obj-sound-unpack", f"obj-dial-{gname}", i, 0)
    box("obj-sound-reset", maxclass="message", text="set 0", numinlets=2, numoutlets=1, outlettype=[""],
        patching_rect=[540.0 + 95.0 * 8, 400.0, 50.0, 22.0])
    wire("obj-sound-unpack", "obj-sound-reset", 0, 0); wire("obj-sound-reset", "obj-sound")

    width = wx + 100.0
    meta = {
        "fileversion": 1, "appversion": {"major": 9, "minor": 0, "revision": 10, "architecture": "x64", "modernui": 1},
        "classnamespace": "box", "rect": [100.0, 100.0, 1500.0, 500.0], "openrect": [0.0, 0.0, 0.0, 169.0],
        "default_fontsize": 10.0, "default_fontname": "Arial Bold", "gridsize": [8.0, 8.0], "boxanimatetime": 500,
        "dependency_cache": [{"name": JS, "type": "TEXT", "implicit": 1}],
        "latency": 0, "is_mpe": 0, "external_mpe_tuning_enabled": 0, "minimum_live_version": "",
        "minimum_max_version": "", "platform_compatibility": 0,
        "project": {"version": 1, "creationdate": 3590052493, "modificationdate": 3590052493,
                    "viewrect": [0.0, 0.0, 300.0, 500.0], "autoorganize": 1, "hideprojectwindow": 1,
                    "showdependencies": 1, "autolocalize": 0, "contents": {"patchers": {}}, "layout": {},
                    "searchpath": {}, "detailsvisible": 0, "amxdtype": 1768515945, "readonly": 0,
                    "devpathtype": 0, "devpath": ".", "sortmode": 0, "viewmode": 0, "includepackages": 0},
        "autosave": 0, "openinpresentation": 1, "devicewidth": width, "parameters": params,
        "boxes": boxes, "lines": lines,
    }
    return {"patcher": meta}


def validate(pat: dict) -> None:
    ids = [b["box"]["id"] for b in pat["patcher"]["boxes"]]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for ln in pat["patcher"].get("lines", []):
        for end in ("source", "destination"):
            assert ln["patchline"][end][0] in ids, f"dangling {end} {ln}"


def write_amxd(path: pathlib.Path, pat: dict) -> None:
    payload = json.dumps(pat, indent=1).encode("utf-8") + b"\x00"
    data = b"ampf" + struct.pack("<I", 4) + b"iiii" + b"meta" + struct.pack("<I", 4) + struct.pack("<I", 1)
    data += b"ptch" + struct.pack("<I", len(payload)) + payload
    path.write_bytes(data)


def build(version: str) -> pathlib.Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    code = (HERE / "pocket303.genexpr").read_text()
    gen = gen_patcher(code); validate(gen)
    dev = device_patcher(gen, version); validate(dev)
    (BUILD / "pocket303.gendsp").write_text(json.dumps(gen, indent=1))
    (BUILD / "Pocket 303.maxpat").write_text(json.dumps(dev, indent=1))
    out = BUILD / "Pocket 303.amxd"
    write_amxd(out, dev)
    shutil.copy2(HERE / JS, BUILD / JS)
    print(f"ok   {out} ({out.stat().st_size} bytes) · {version}")
    return out


def install() -> None:
    USER_DEVICES.mkdir(parents=True, exist_ok=True)
    for name in ("Pocket 303.amxd", JS):
        target = USER_DEVICES / name
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        shutil.copy2(BUILD / name, target)
        print(f"ok   installed {target}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="copy the device + js into the User Library")
    ap.add_argument("--version", default=None, help="stamp shown on the device (default: date + git sha)")
    a = ap.parse_args()
    build(a.version or version_stamp())
    if a.install:
        install()
