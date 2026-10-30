"""Build `Pocket Kick.amxd` + `pocketkick.gendsp` from pocketkick.genexpr.

Reuses the Pocket 101's slider-panel device builder and the Pocket 303's container/gen~ plumbing and mono
note js (installed here as pocketkick_notes.js).

    python3 m4l/pocketkick/build_device.py --install --version 0.1
"""
import argparse, importlib.util, json, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"
USER_DEVICES = pathlib.Path.home() / "Music/Ableton/User Library/Devices/Pocket Kick"
JS = "pocketkick_notes.js"
JS_SOURCE = HERE.parent / "pocket303" / "pocket303_notes.js"

_spec = importlib.util.spec_from_file_location("pocket101_build", HERE.parent / "pocket101" / "build_device.py")
p101 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(p101)
p303 = p101.p303

# ("slider", gen param, label, min, max, initial, unitstyle, converter) / ("menu", gen param, label, [items], initial)
SECTIONS = [
    ("BODY",   [("menu", "body", "ENGINE", ["Sine", "303"], 0),
                ("menu", "keytrack", "KEY", ["Fixed", "Track"], 0),
                ("slider", "tune", "TUNE", 0, 100, 44, 5, "/ 100."),
                ("slider", "sweep", "PITCH", 0, 100, 50, 5, "/ 100."),
                ("slider", "sweep_dec", "P DEC", 0, 100, 40, 5, "/ 100.")]),
    ("303",    [("slider", "cutoff", "CUT", 0, 100, 50, 5, "/ 100."),
                ("slider", "resonance", "RES", 0, 100, 70, 5, "/ 100.")]),
    ("AMP",    [("slider", "hold", "HOLD", 0, 100, 30, 5, "/ 100."),
                ("slider", "decay", "DECAY", 0, 100, 25, 5, "/ 100."),
                ("slider", "accent", "ACCENT", 0, 100, 50, 5, "/ 100.")]),
    ("ATTACK", [("slider", "click", "CLICK", 0, 100, 60, 5, "/ 100."),
                ("slider", "mix_noise", "NOISE", 0, 100, 15, 5, "/ 100."),
                ("slider", "noise_dec", "N DEC", 0, 100, 25, 5, "/ 100.")]),
    ("OUT",    [("slider", "drive", "DRIVE", 0, 100, 55, 5, "/ 100."),
                ("slider", "tone", "TONE", 0, 100, 80, 5, "/ 100."),
                ("slider", "volume", "VOL", 0, 100, 80, 5, "/ 100.")]),
]
# Sound presets in control units; mirrors SOUNDS in proto/render.py (gen~ values).
PRESETS = [
    ("Techno",   dict(body=0, tune=44, sweep=50, sweep_dec=40, hold=30, decay=25, click=60, mix_noise=15, noise_dec=25, drive=55, tone=80, accent=50)),
    ("Acid 303", dict(body=1, tune=35, cutoff=55, resonance=85, sweep=60, sweep_dec=70, hold=20, decay=40, click=50, mix_noise=0, drive=60, tone=80, accent=50)),
    ("Rumble",   dict(body=0, tune=25, sweep=30, sweep_dec=60, hold=40, decay=85, click=30, mix_noise=10, noise_dec=50, drive=70, tone=50, accent=50)),
    ("Boing",    dict(body=1, tune=40, cutoff=75, resonance=100, sweep=70, sweep_dec=70, hold=10, decay=60, click=70, mix_noise=0, drive=30, tone=90, accent=50)),
]


def build(version: str) -> pathlib.Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    code = (HERE / "pocketkick.genexpr").read_text()
    gen = p303.gen_patcher(code); p303.validate(gen)
    dev = p101.device_patcher(gen, version, title="POCKET KICK", varname="pocketkick", js=JS,
                              sections=SECTIONS, presets=PRESETS, prefix_labels=set())
    p303.validate(dev)
    (BUILD / "pocketkick.gendsp").write_text(json.dumps(gen, indent=1))
    (BUILD / "Pocket Kick.maxpat").write_text(json.dumps(dev, indent=1))
    out = BUILD / "Pocket Kick.amxd"
    p303.write_amxd(out, dev)
    shutil.copy2(JS_SOURCE, BUILD / JS)
    print(f"ok   {out} ({out.stat().st_size} bytes) · {version} · width {dev['patcher']['devicewidth']:.0f}")
    return out


def install() -> None:
    USER_DEVICES.mkdir(parents=True, exist_ok=True)
    for name in ("Pocket Kick.amxd", JS):
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
