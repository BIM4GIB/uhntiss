"""Drum-classifier feature extraction — the single source of truth.

These 10 features were chosen by held-out-take cross-validation
(``eval/featurelab.py``), not hand-picked. They generalize to a fresh
performance far better than the original 5 (held-out bb100 0.59 -> 0.96, with
zero snare<->kick confusion) because they lean on **loudness/mic-invariant
timbre shape** (spectral contrast) plus late-window brightness and one cepstral
coefficient, rather than absolute spectral magnitudes that drift with mic/gain.

The classifier (``classify._classify``), the trainer (``eval/train_classifier``)
and the feature lab all extract through ``drum_features`` so they can never
disagree on the feature definition.
"""

from __future__ import annotations

import numpy as np

from mouthflow import signal

FEATURES = [
    "zcr_late",
    "centroid_late",
    "mfcc3",
    "contrast0",
    "contrast1",
    "contrast2",
    "contrast3",
    "contrast4",
    "contrast5",
    "contrast6",
]


def _safe(x: float) -> float:
    x = float(x)
    return x if np.isfinite(x) else 0.0


def drum_features(audio: np.ndarray, sr: int, t: float) -> list[float]:
    """10-feature vector for the onset at time ``t`` (seconds)."""
    import librosa

    # (a) Late-window brightness: zcr + centroid measured 60 ms INTO the onset
    # (how bright/noisy the hit still is after the attack) generalizes across
    # performers better than the instantaneous attack spectrum.
    fl = signal.features_at(audio, sr, t + 0.060)

    # (b) One mid cepstral coefficient over the 0-120 ms window — coarse
    # log-spectral envelope; adds the snare-vs-kick separation contrast misses.
    frame = audio[int(t * sr) : int((t + 0.120) * sr)]
    if len(frame) < 64:
        frame = np.zeros(64, dtype=np.float32)
    nfft = min(1024, 1 << (len(frame) - 1).bit_length())
    mfcc = librosa.feature.mfcc(
        y=frame, sr=sr, n_mfcc=8, n_fft=nfft, hop_length=max(64, nfft // 2)
    ).mean(axis=1)

    # (c) 7 spectral-contrast bands (fmin=200 Hz) over a 200 ms window — peak-to-
    # valley dB per sub-band is loudness/mic-invariant timbre SHAPE; the
    # load-bearing generalizer.
    s = int(t * sr)
    fr = audio[s : min(s + int(0.200 * sr), len(audio))]
    if len(fr) < 64:
        fr = np.pad(fr, (0, 64 - len(fr)))
    n_fft = min(1024, 1 << max(63, len(fr) - 1).bit_length())
    try:
        contrast = [
            _safe(v)
            for v in librosa.feature.spectral_contrast(
                y=fr, sr=sr, n_fft=n_fft, n_bands=6, fmin=200.0
            ).mean(axis=1)
        ]
    except Exception:  # pragma: no cover — degenerate frame
        contrast = [0.0] * 7

    return [_safe(fl["zcr"]), _safe(fl["centroid"]), _safe(float(mfcc[3])), *contrast]
