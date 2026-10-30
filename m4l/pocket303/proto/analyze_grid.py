"""Grid-based per-note analysis: usage analyze_grid.py file.wav bpm step count [offset_s]
Reports RMS envelope (10 ms) for the first 2 steps, and per note the top spectral peak trajectory
(300-4000 Hz, every ~11.6 ms over 180 ms), its prominence over the band median, and f0."""
import sys, json, numpy as np, librosa
path, bpm, step, count = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
offset = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
y, sr = librosa.load(path, sr=44100, mono=True)
spb = 60.0 / bpm
N, H = 2048, 128
S = np.abs(librosa.stft(y, n_fft=N, hop_length=H))**2
freqs = np.linspace(0, sr/2, S.shape[0])
lo, hi = int(300/(sr/N)), int(4000/(sr/N))
# envelope of first two steps in 10 ms frames
n10 = int(0.01*sr); seg = y[int(offset*sr): int(offset*sr) + int(2*step*spb*sr)]
env = [round(float(20*np.log10(np.sqrt(np.mean(seg[i:i+n10]**2))+1e-9)),1) for i in range(0, len(seg)-n10, n10)]
print("rms10ms first 2 steps:", env)
f0 = librosa.yin(y, fmin=50, fmax=1500, sr=sr, frame_length=2048, hop_length=H)
notes = []
for n in range(count):
    t = offset + n*step*spb; i = int(t*sr/H) + 1
    traj, prom = [], []
    for f in range(i, min(i+62, S.shape[1]), 4):
        k = lo + int(np.argmax(S[lo:hi, f])); traj.append(int(round(freqs[k])))
        sg = S[lo:hi, f]; prom.append(round(float(10*np.log10(sg.max()+1e-12) - 10*np.log10(np.median(sg)+1e-12)),1))
    lvl = round(float(10*np.log10(S[:, i:i+16].sum()+1e-12)),1)
    notes.append({"n": n, "f0": round(float(np.median(f0[i+2:i+10])),1), "lvl": lvl, "peak": traj[:10], "prom": prom[:6]})
print(json.dumps({"file": path.split("/")[-1][-30:], "rms_dbfs": round(float(20*np.log10(np.sqrt(np.mean(y**2))+1e-12)),1), "notes": notes}, indent=None))
