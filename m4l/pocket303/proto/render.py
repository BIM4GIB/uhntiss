"""Render the three test clips through the C prototype. usage: render.py <tag> [param overrides k=v ...]"""
import json, subprocess, sys, pathlib, os
HERE = pathlib.Path(__file__).parent
clips = json.load(open(HERE / "test_clips.json"))
tag = sys.argv[1]
over = dict(a.split("=") for a in sys.argv[2:])
SOUNDS = {
 "riff":   dict(clip="riff (Acid Trojan, 136)", length=4, bpm=136,
                p=dict(cutoff=0.30, resonance=0.79, envmod=0.67, decay=0.20, accent=0.79, slide=0.3, volume=0.8, wave=0)),
 "lasers": dict(clip="laser arp (Insurgency)", length=4, bpm=136,
                p=dict(cutoff=0.30, resonance=0.93, envmod=0.71, decay=0.25, accent=0.87, slide=0.3, volume=0.8, wave=0)),
 "sweep":  dict(clip="sweep A#2 pulses (Insurgency)", length=128, bpm=136,
                p=dict(cutoff=0.16, resonance=0.9, envmod=0.2, decay=0.79, accent=0.55, slide=0.3, volume=0.8, wave=0),
                auto=[(0, "cutoff", 0.16), (112, "cutoff", 0.60), (124, "cutoff", 0.60), (128, "cutoff", 0.16)]),
}
# the laser line as rebuilt for s4+ (record's notes), not the old test_clips arp
LASER_PATTERN = [69,70,73,69,72,70,73,73,72,70,73,72,73,72,72,70]; LASER_ACCENTS={0,3,6,10,13}; LASER_SLIDES={7,11,14}
outdir = HERE / f"renders_{tag}"; outdir.mkdir(exist_ok=True)
only = over.pop("only", None)
for name, s in SOUNDS.items():
    if only and name != only: continue
    p = dict(s["p"]); p.update({k: float(v) for k, v in over.items() if k in p})
    lines = [f"R 44100", f"T {s['bpm']}", f"L {s['length']}", "B 64", f"O {outdir / (name + '.wav')}"]
    lines += [f"P {k} {v}" for k, v in p.items()]
    for a in s.get("auto", []): lines.append(f"A {a[0]} {a[1]} {a[2]}")
    c = clips[s["clip"]]
    if name == "lasers":
        for i, n in enumerate(LASER_PATTERN):
            lines.append(f"N {i*0.25} {0.31 if i in LASER_SLIDES else 0.2} {n} {127 if i in LASER_ACCENTS else 90}")
    elif "pulse" in c:
        pu = c["pulse"]
        for i in range(pu["count"] * (s["length"] // 16)):
            lines.append(f"N {i*pu['step']} {pu['duration']} {pu['pitch']} {pu['velocity']}")
    else:
        for n in c["notes"]:
            if not n.get("mute"): lines.append(f"N {n['start_time']} {n['duration']} {n['pitch']} {n['velocity']}")
    script = outdir / f"{name}.txt"; script.write_text("\n".join(lines) + "\n")
    print(subprocess.run([str(HERE / "host"), str(script)], capture_output=True, text=True).stdout.strip())
