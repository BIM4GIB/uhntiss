"""Render the Pocket 101 test bassline through the C prototype for each Sound preset.
usage: render.py <tag> [only=<sound>] [param=value ...]      -> renders_<tag>/<sound>.wav
Param values are the gen~ values (0..1, switches as 0..3); see voice.h PARAMS."""
import pathlib, subprocess, sys
HERE = pathlib.Path(__file__).parent
tag = sys.argv[1]
over = dict(a.split("=") for a in sys.argv[2:])
only = over.pop("only", None)
BPM, LENGTH = 136, 4
# 1-bar test bassline in D (the Acid Trojan riff's key): 16ths, an octave jump, a legato pair at the end.
BASSLINE = [(0.0, 0.2, 38), (0.5, 0.2, 38), (0.75, 0.15, 50), (1.0, 0.2, 38), (1.5, 0.2, 41), (1.75, 0.2, 38),
            (2.0, 0.2, 36), (2.5, 0.2, 38), (3.0, 0.2, 38), (3.25, 0.15, 50), (3.5, 0.2, 45), (3.75, 0.4, 43)]
# Sound presets (gen~ values). Same numbers as PRESETS in build_device.py, which stores them in dial units.
SOUNDS = {
    "sub":   dict(oct_range=0, mix_saw=0.6, mix_pulse=0, mix_sub=1.0, sub_type=0, cutoff=0.35, resonance=0.1,
                  env_vcf=0.3, kybd=0.5, attack=0, decay=0.65, sustain=0.3, release=0.2, porta=0, porta_mode=0),
    "acid":  dict(oct_range=1, mix_saw=1.0, mix_pulse=0, mix_sub=0.7, sub_type=0, cutoff=0.3, resonance=0.55,
                  env_vcf=0.55, kybd=0.3, attack=0, decay=0.55, sustain=0, release=0.15, porta=0.3, porta_mode=1),
    "pulse": dict(oct_range=1, mix_saw=0, mix_pulse=1.0, pw=0.4, pwm_src=1, mix_sub=0.8, sub_type=1, cutoff=0.4,
                  resonance=0.25, env_vcf=0.4, kybd=0.5, attack=0, decay=0.6, sustain=0.2, release=0.15, porta=0, porta_mode=0),
    "zap":   dict(oct_range=2, mix_saw=1.0, mix_pulse=0, mix_sub=0, cutoff=0.2, resonance=0.85, env_vcf=0.9,
                  kybd=0, attack=0, decay=0.35, sustain=0, release=0.1, porta=0, porta_mode=0),
}
outdir = HERE / f"renders_{tag}"; outdir.mkdir(exist_ok=True)
for name, p in SOUNDS.items():
    if only and name != only: continue
    p = dict(p); p.update({k: float(v) for k, v in over.items()})
    lines = ["R 44100", f"T {BPM}", f"L {LENGTH}", "B 64", f"O {outdir / (name + '.wav')}"]
    lines += [f"P {k} {v}" for k, v in p.items()]
    lines += [f"N {s} {d} {n} 100" for s, d, n in BASSLINE]
    script = outdir / f"{name}.txt"; script.write_text("\n".join(lines) + "\n")
    print(subprocess.run([str(HERE / "host"), str(script)], capture_output=True, text=True).stdout.strip())
