import sounddevice as sd, soundfile as sf
ref, sr = sf.read('mimic/reference.wav')
if ref.ndim == 1:
    ref = ref.reshape(-1, 1)
try:
    rec = sd.playrec(ref, samplerate=sr, channels=1)
    sd.wait()
    sf.write('mimic/mimic.wav', rec, sr, subtype='PCM_16')
    print(f'OK recorded {len(rec)/sr:.1f}s -> mimic/mimic.wav')
except Exception as e:
    import traceback; traceback.print_exc()
