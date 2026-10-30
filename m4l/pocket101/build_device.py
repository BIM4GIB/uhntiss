"""Build `Pocket 101.amxd` (Max for Live instrument) + `pocket101.gendsp` from pocket101.genexpr.

Shares the container/gen~ plumbing with the Pocket 303 builder (imported from ../pocket303/build_device.py)
and its mono note js (installed next to this device as pocket101_notes.js).

    python m4l/pocket101/build_device.py --install --version 0.1

Panel: SH-101 style vertical live.sliders in sections, live.menus for the switches, a Sound umenu whose
presets write every slider and menu. All control values go  slider -> / 100. -> prepend <param> -> gen~
(menus: index -> prepend <param> -> gen~).
"""
import argparse, importlib.util, json, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"
USER_DEVICES = pathlib.Path.home() / "Music/Ableton/User Library/Devices/Pocket 101"
JS = "pocket101_notes.js"
JS_SOURCE = HERE.parent / "pocket303" / "pocket303_notes.js"

_spec = importlib.util.spec_from_file_location("pocket303_build", HERE.parent / "pocket303" / "build_device.py")
p303 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(p303)

# ("slider", gen param, label, min, max, initial, unitstyle, converter)  unitstyle 5 = %, 7 = semitones
# ("menu",   gen param, label, [items], initial)
SECTIONS = [
    ("VCO",   [("menu", "oct_range", "RANGE", ["16'", "8'", "4'", "2'"], 1),
               ("menu", "pwm_src", "PWM", ["LFO", "MAN", "ENV"], 1),
               ("slider", "lfo_vco", "MOD", 0, 100, 0, 5, "/ 100."),
               ("slider", "pw", "PW", 0, 100, 0, 5, "/ 100.")]),
    ("MIXER", [("slider", "mix_pulse", "PULSE", 0, 100, 0, 5, "/ 100."),
               ("slider", "mix_saw", "SAW", 0, 100, 100, 5, "/ 100."),
               ("slider", "mix_sub", "SUB", 0, 100, 70, 5, "/ 100."),
               ("menu", "sub_type", "SUB TYPE", ["-1 oct", "-2 oct", "-2 pulse"], 0),
               ("slider", "mix_noise", "NOISE", 0, 100, 0, 5, "/ 100.")]),
    ("VCF",   [("slider", "cutoff", "FREQ", 0, 100, 30, 5, "/ 100."),
               ("slider", "resonance", "RES", 0, 100, 55, 5, "/ 100."),
               ("slider", "env_vcf", "ENV", 0, 100, 55, 5, "/ 100."),
               ("slider", "lfo_vcf", "MOD", 0, 100, 0, 5, "/ 100."),
               ("slider", "kybd", "KYBD", 0, 100, 30, 5, "/ 100.")]),
    ("ENV",   [("menu", "vca_mode", "VCA", ["ENV", "GATE"], 0),
               ("slider", "attack", "A", 0, 100, 0, 5, "/ 100."),
               ("slider", "decay", "D", 0, 100, 55, 5, "/ 100."),
               ("slider", "sustain", "S", 0, 100, 0, 5, "/ 100."),
               ("slider", "release", "R", 0, 100, 15, 5, "/ 100.")]),
    ("LFO",   [("menu", "lfo_wave", "WAVE", ["Tri", "Square", "S&H", "Random"], 0),
               ("slider", "lfo_rate", "RATE", 0, 100, 50, 5, "/ 100.")]),
    ("PORTA", [("menu", "porta_mode", "MODE", ["Off", "Auto", "On"], 1),
               ("slider", "porta", "TIME", 0, 100, 30, 5, "/ 100.")]),
    ("OUT",   [("slider", "tuning", "TUNE", -12, 12, 0, 7, "expr ($f1+12.)/24."),
               ("slider", "volume", "VOL", 0, 100, 80, 5, "/ 100.")]),
]
CONTROLS = [c for _, cs in SECTIONS for c in cs]           # device order = preset order
# Sound presets in control units (sliders 0..100 / semitones, menus = index), same order as CONTROLS.
# Mirrors SOUNDS in proto/render.py (gen~ values).
PRESETS = [
    ("Sub bass",   dict(oct_range=0, mix_saw=60, mix_pulse=0, mix_sub=100, sub_type=0, cutoff=35, resonance=10,
                        env_vcf=30, kybd=50, attack=0, decay=65, sustain=30, release=20, porta=0, porta_mode=0)),
    ("Acid bass",  dict(oct_range=1, mix_saw=100, mix_pulse=0, mix_sub=70, sub_type=0, cutoff=30, resonance=55,
                        env_vcf=55, kybd=30, attack=0, decay=55, sustain=0, release=15, porta=30, porta_mode=1)),
    ("Pulse bass", dict(oct_range=1, mix_saw=0, mix_pulse=100, pw=40, pwm_src=1, mix_sub=80, sub_type=1, cutoff=40,
                        resonance=25, env_vcf=40, kybd=50, attack=0, decay=60, sustain=20, release=15, porta=0, porta_mode=0)),
    ("Zap",        dict(oct_range=2, mix_saw=100, mix_pulse=0, mix_sub=0, cutoff=20, resonance=85, env_vcf=90,
                        kybd=0, attack=0, decay=35, sustain=0, release=10, porta=0, porta_mode=0)),
]
TITLE_W, SLIDER_W, SLIDER_PITCH, MENU_W, MENU_COL_W, GAP = 84.0, 24.0, 30.0, 58.0, 64.0, 8.0
SLIDER_Y, SLIDER_H = 20.0, 138.0


def ctrl_default(c):
    return c[5] if c[0] == "slider" else c[4]


PREFIX_LABELS = {"MOD", "ENV", "A", "D", "S", "R", "MODE", "WAVE"}   # labels that get their section name in Live


def device_patcher(gen: dict, version: str, *, title: str = "POCKET 101", varname: str = "pocket101", js: str = JS,
                   sections=SECTIONS, presets=PRESETS, prefix_labels=PREFIX_LABELS) -> dict:
    """Slider-panel M4L instrument around a gen~ core. Reused by the other Pocket devices (import this module)."""
    controls = [c for _, cs in sections for c in cs]
    boxes, lines, params = [], [], {}

    def longname_of(sname, label):
        return f"{sname} {label}" if label in prefix_labels else label

    def box(bid, **kw):
        kw["id"] = bid; boxes.append({"box": kw})

    def wire(src, dst, so=0, di=0):
        lines.append({"patchline": {"source": [src, so], "destination": [dst, di]}})

    def newobj(bid, text, rect, nin, nout, outtypes, **extra):
        box(bid, maxclass="newobj", text=text, patching_rect=rect, numinlets=nin, numoutlets=nout, outlettype=outtypes, **extra)

    def comment(bid, text, prect, size=9.0, **extra):
        box(bid, maxclass="comment", text=text, fontsize=size, patching_rect=[prect[0], 600.0, prect[2], prect[3]],
            numinlets=1, numoutlets=0, presentation=1, presentation_rect=prect, **extra)

    # --- MIDI path (identical to the 303) ---
    newobj("obj-midiin", "midiin", [30.0, 30.0, 50.0, 22.0], 1, 1, ["int"])
    newobj("obj-parse", "midiparse", [30.0, 70.0, 92.5, 22.0], 1, 8, ["", "", "", "int", "int", "", "int", ""])
    newobj("obj-js", f"js {js}", [30.0, 110.0, 140.0, 22.0], 1, 1, [""], saved_object_attributes={"filename": js, "parameter_enable": 0})
    newobj("obj-unpack", "unpack f f f f f", [30.0, 150.0, 120.0, 22.0], 1, 5, ["float"] * 5)
    wire("obj-midiin", "obj-parse"); wire("obj-parse", "obj-js"); wire("obj-js", "obj-unpack")
    for i in range(5):
        wire("obj-unpack", "obj-gen", i, i)
    box("obj-gen", maxclass="newobj", text="gen~", patching_rect=[30.0, 330.0, 200.0, 22.0], numinlets=5, numoutlets=3,
        outlettype=["signal", "signal", "signal"], varname=varname,
        saved_object_attributes={"exportfolder": "", "exportname": varname}, patcher=gen["patcher"])
    newobj("obj-plugout", "plugout~", [30.0, 400.0, 60.0, 22.0], 2, 2, ["signal", "signal"])
    wire("obj-gen", "obj-plugout", 0, 0); wire("obj-gen", "obj-plugout", 1, 1)

    # --- title column: name, stamp, Sound menu ---
    comment("obj-title", title, [8.0, 4.0, TITLE_W, 18.0], 11.0, fontname="Arial Bold")
    comment("obj-stamp", f"gen~ · {version}", [8.0, 22.0, TITLE_W, 14.0], 8.0, textcolor=[0.6, 0.6, 0.6, 1.0])
    items = ["Sound…"] + [n for n, _ in presets]
    box("obj-sound", maxclass="umenu", items=items, numinlets=1, numoutlets=3, outlettype=["int", "", ""],
        patching_rect=[400.0, 500.0, 120.0, 20.0], presentation=1, presentation_rect=[8.0, 44.0, TITLE_W - 12.0, 20.0])
    comment("obj-sound-label", "presets write\nthe sliders", [8.0, 66.0, TITLE_W, 26.0], 8.0, textcolor=[0.6, 0.6, 0.6, 1.0])

    # --- sections ---
    x = TITLE_W + GAP
    px = 400.0
    for si, (sname, sec_controls) in enumerate(sections):
        x_start = x
        menu_y = SLIDER_Y
        menu_col_used = False
        for c in sec_controls:
            kind, gname, label = c[0], c[1], c[2]
            if kind == "slider":
                _, _, _, mn, mx, init, unit, conv = c
                bid = f"obj-ctl-{gname}"
                box(bid, maxclass="live.slider", varname=gname, parameter_enable=1, numinlets=1, numoutlets=2,
                    outlettype=["", "float"], patching_rect=[px, 80.0, SLIDER_W, SLIDER_H], presentation=1,
                    presentation_rect=[x, SLIDER_Y, SLIDER_W, SLIDER_H], showname=1, shownumber=1,
                    saved_attribute_attributes={"valueof": {
                        "parameter_longname": longname_of(sname, label),
                        "parameter_shortname": label, "parameter_type": 0, "parameter_mmin": mn, "parameter_mmax": mx,
                        "parameter_initial": [init], "parameter_initial_enable": 1, "parameter_unitstyle": unit}})
                params[bid] = [longname_of(sname, label), label, 0]
                cid, pid = f"obj-conv-{gname}", f"obj-prep-{gname}"
                newobj(cid, conv, [px, 230.0, 90.0, 22.0], 2 if conv.startswith("/") else 1, 1, [""])
                newobj(pid, f"prepend {gname}", [px, 270.0, 90.0, 22.0], 1, 1, [""])
                wire(bid, cid); wire(cid, pid); wire(pid, "obj-gen")
                x += SLIDER_PITCH; px += 95.0
            else:
                _, _, _, menu_items, init = c
                if not menu_col_used:
                    menu_col_used = True
                    mx_ = x
                else:
                    mx_ = menu_x
                menu_x = mx_
                bid = f"obj-ctl-{gname}"
                comment(f"obj-lbl-{gname}", label, [menu_x, menu_y, MENU_W, 12.0], 7.0, textcolor=[0.6, 0.6, 0.6, 1.0])
                box(bid, maxclass="live.menu", varname=gname, parameter_enable=1, numinlets=1, numoutlets=3,
                    outlettype=["", "", "float"], items=menu_items, patching_rect=[px, 80.0, MENU_W, 20.0],
                    presentation=1, presentation_rect=[menu_x, menu_y + 12.0, MENU_W, 20.0],
                    saved_attribute_attributes={"valueof": {
                        "parameter_longname": longname_of(sname, label),
                        "parameter_shortname": label, "parameter_type": 2, "parameter_mmin": 0,
                        "parameter_mmax": len(menu_items) - 1, "parameter_initial": [init], "parameter_initial_enable": 1,
                        "parameter_enum": menu_items}})
                params[bid] = [longname_of(sname, label), label, 0]
                pid = f"obj-prep-{gname}"
                newobj(pid, f"prepend {gname}", [px, 270.0, 90.0, 22.0], 1, 1, [""])
                wire(bid, pid); wire(pid, "obj-gen")
                menu_y += 36.0; px += 95.0
                if menu_y + 32.0 > SLIDER_Y + SLIDER_H:          # column full: start another
                    menu_col_used = False
                    menu_y = SLIDER_Y
                    x += MENU_COL_W
        if menu_col_used:
            x += MENU_COL_W
        comment(f"obj-sec-{si}", sname, [x_start, 4.0, x - x_start, 14.0], 8.0, fontname="Arial Bold", textcolor=[0.75, 0.75, 0.75, 1.0])
        x += GAP
    width = x

    # --- Sound presets: umenu -> sel -> message lists -> unpack -> every control (then reset the menu) ---
    n = len(presets)
    newobj("obj-sound-sel", "sel " + " ".join(str(i + 1) for i in range(n)), [400.0, 540.0, 90.0, 22.0], 1, n + 1, ["bang"] * n + [""])
    wire("obj-sound", "obj-sound-sel")
    spec = " ".join("f" if c[0] == "slider" else "i" for c in controls)
    newobj("obj-sound-unpack", "unpack " + spec, [400.0, 620.0, 600.0, 22.0], 1, len(controls), ["float" if c[0] == "slider" else "int" for c in controls])
    for i, (name, values) in enumerate(presets):
        unknown = set(values) - {c[1] for c in controls}
        assert not unknown, f"preset {name}: unknown params {unknown}"
        vals = [values.get(c[1], ctrl_default(c)) for c in controls]
        mid = f"obj-sound-msg{i}"
        box(mid, maxclass="message", text=" ".join(str(v) for v in vals), numinlets=2, numoutlets=1, outlettype=[""],
            patching_rect=[400.0 + 160.0 * i, 580.0, 150.0, 22.0])
        wire("obj-sound-sel", mid, i, 0); wire(mid, "obj-sound-unpack")
    for i, c in enumerate(controls):
        wire("obj-sound-unpack", f"obj-ctl-{c[1]}", i, 0)
    box("obj-sound-reset", maxclass="message", text="set 0", numinlets=2, numoutlets=1, outlettype=[""], patching_rect=[400.0, 660.0, 50.0, 22.0])
    wire("obj-sound-unpack", "obj-sound-reset", 0, 0); wire("obj-sound-reset", "obj-sound")

    meta = {
        "fileversion": 1, "appversion": {"major": 9, "minor": 0, "revision": 10, "architecture": "x64", "modernui": 1},
        "classnamespace": "box", "rect": [100.0, 100.0, 1500.0, 700.0], "openrect": [0.0, 0.0, 0.0, 169.0],
        "default_fontsize": 10.0, "default_fontname": "Arial Bold", "gridsize": [8.0, 8.0], "boxanimatetime": 500,
        "dependency_cache": [{"name": js, "type": "TEXT", "implicit": 1}],
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


def build(version: str) -> pathlib.Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    code = (HERE / "pocket101.genexpr").read_text()
    gen = p303.gen_patcher(code); p303.validate(gen)
    dev = device_patcher(gen, version); p303.validate(dev)
    (BUILD / "pocket101.gendsp").write_text(json.dumps(gen, indent=1))
    (BUILD / "Pocket 101.maxpat").write_text(json.dumps(dev, indent=1))
    out = BUILD / "Pocket 101.amxd"
    p303.write_amxd(out, dev)
    shutil.copy2(JS_SOURCE, BUILD / JS)
    print(f"ok   {out} ({out.stat().st_size} bytes) · {version} · width {dev['patcher']['devicewidth']:.0f}")
    return out


def install() -> None:
    USER_DEVICES.mkdir(parents=True, exist_ok=True)
    for name in ("Pocket 101.amxd", JS):
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
    build(a.version or p303.version_stamp())
    if a.install:
        install()
