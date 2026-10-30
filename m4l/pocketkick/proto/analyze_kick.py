"""Kick analysis: usage analyze_kick.py file.wav bpm step count
Per hit: peak dBFS, time to -20 dB, f0 (Hz) at 5/20/50/100/200 ms (YIN 25..600 Hz), spectral centroid at 5 and 80 ms."""
import sys, numpy as np, librosa
path, bpm, step, count = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
y, sr = librosa.load(path, sr=44100, mono=True)
spb = 60.0 / bpm
H = 128
f0 = librosa.yin(y, fmin=25, fmax=600, sr=sr, frame_length=2048, hop_length=H)
S = np.abs(librosa.stft(y, n_fft=1024, hop_length=H))
cent = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
print(f"{path.split('/')[-1]}  rms {20*np.log10(np.sqrt(np.mean(y**2))+1e-12):.1f} dBFS")
for n in range(count):
    t0 = n * step * spb; i0 = int(t0 * sr); seg = y[i0:i0 + int(min(step * spb, 0.6) * sr)]
    if len(seg) < 2048: break
    env = np.array([np.sqrt(np.mean(seg[j:j+220]**2)) for j in range(0, len(seg)-220, 220)])   # 5 ms frames
    pk = 20*np.log10(np.abs(seg).max()+1e-12)
    epk = env.max(); below = np.where(env < epk * 0.1)[0]; after = below[below > env.argmax()]
    t20 = (after[0] * 5) if len(after) else -1
    fr = lambda ms: int((t0 + ms/1000) * sr / H)
    f0s = [round(float(f0[fr(ms)]), 1) for ms in (5, 20, 50, 100, 200)]
    print(f"hit {n}: peak {pk:5.1f} dBFS  t-20dB {t20:4d} ms  f0@5/20/50/100/200ms {f0s}  centroid@5ms {cent[fr(5)]:.0f} Hz @80ms {cent[fr(80)]:.0f} Hz")
