"""Render the Pocket Kick test pattern through the C prototype for each Sound preset.
usage: render.py <tag> [only=<sound>] [param=value ...]     -> renders_<tag>/<sound>.wav   (gen~ values)"""
import pathlib, subprocess, sys
HERE = pathlib.Path(__file__).parent
tag = sys.argv[1]
over = dict(a.split("=") for a in sys.argv[2:])
only = over.pop("only", None)
BPM, LENGTH = 136, 4
# four to the floor on C1, beat 1 accented (vel >= 100 = accent), plus a pitched pickup pair for key tracking
PATTERN = [(0.0, 0.25, 36, 127), (1.0, 0.25, 36, 90), (2.0, 0.25, 36, 90), (3.0, 0.25, 36, 90), (3.5, 0.2, 38, 90), (3.75, 0.2, 41, 90)]
SOUNDS = {   # gen~ values; mirrored in build_device.py PRESETS (control units)
    "techno": dict(body=0, tune=0.44, sweep=0.5, sweep_dec=0.4, hold=0.3, decay=0.25, click=0.6, mix_noise=0.15, noise_dec=0.25, drive=0.55, tone=0.8, accent=0.5),
    "acid":   dict(body=1, tune=0.35, cutoff=0.55, resonance=0.85, sweep=0.6, sweep_dec=0.7, hold=0.2, decay=0.4, click=0.5, mix_noise=0, drive=0.6, tone=0.8, accent=0.5),
    "rumble": dict(body=0, tune=0.25, sweep=0.3, sweep_dec=0.6, hold=0.4, decay=0.85, click=0.3, mix_noise=0.1, noise_dec=0.5, drive=0.7, tone=0.5, accent=0.5),
    "boing":  dict(body=1, tune=0.4, cutoff=0.75, resonance=1.0, sweep=0.7, sweep_dec=0.7, hold=0.1, decay=0.6, click=0.7, mix_noise=0, drive=0.3, tone=0.9, accent=0.5),
}
outdir = HERE / f"renders_{tag}"; outdir.mkdir(exist_ok=True)
for name, p in SOUNDS.items():
    if only and name != only: continue
    p = dict(p); p.update({k: float(v) for k, v in over.items()})
    lines = ["R 44100", f"T {BPM}", f"L {LENGTH}", "B 64", f"O {outdir / (name + '.wav')}"]
    lines += [f"P {k} {v}" for k, v in p.items()]
    lines += [f"N {s} {d} {n} {v}" for s, d, n, v in PATTERN]
    script = outdir / f"{name}.txt"; script.write_text("\n".join(lines) + "\n")
    print(subprocess.run([str(HERE / "host"), str(script)], capture_output=True, text=True).stdout.strip())
