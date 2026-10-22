"""Drum-classifier feature extraction — the single source of truth.

Two feature sets, selected by the model file's ``feature_set`` key:

``drum10`` — the original 10, chosen by held-out-take cross-validation on one
performer (``eval/featurelab.py``): loudness/mic-invariant spectral contrast,
late-window brightness and one cepstral coefficient.

``all123`` — ``drum10`` + 13 MFCC mean/std/delta (39) + a level-normalised
log-mel shape at three moments of the hit with decay slopes (74). Chosen on
the AVP cross-performer dataset (``eval/avp_eval.py``, 25 performers): per-user
k-NN enrollment 75.1 -> 83.8 %, voices-seen improvisations 67.1 -> 79.1 %, and
no regression on this performer's own takes (held-out 0.734 -> 0.739).

The classifier (``classify``), the trainers (``eval/train_classifier``,
``eval/train_global``) and the labs all extract through ``extract(name, …)``
so they can never disagree on a feature definition.
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


# --- all123: drum10 + MFCC statistics + log-mel shape ----------------------------

def _window(audio: np.ndarray, sr: int, t: float, dur: float) -> np.ndarray:
    s = int(t * sr)
    fr = audio[s : min(s + int(dur * sr), len(audio))]
    if len(fr) < int(dur * sr):
        fr = np.pad(fr, (0, int(dur * sr) - len(fr)))
    return np.asarray(fr, dtype=np.float32)


def mfcc_stats(audio: np.ndarray, sr: int, t: float) -> list[float]:
    """13 MFCCs over 0-150 ms: mean, std and mean delta — 39 dims."""
    import librosa

    fr = _window(audio, sr, t, 0.150)
    m = librosa.feature.mfcc(y=fr, sr=sr, n_mfcc=13, n_fft=1024, hop_length=256)
    d = librosa.feature.delta(m, width=3) if m.shape[1] >= 3 else np.zeros_like(m)
    return [_safe(v) for v in (*m.mean(1), *m.std(1), *d.mean(1))]


_MEL_EDGES = (0.0, 0.030, 0.090, 0.200)  # attack / body / tail slices (s)
_N_MELS = 24


def logmel3(audio: np.ndarray, sr: int, t: float) -> list[float]:
    """Level-invariant log-mel shape at three moments of the hit + decay slopes.

    Each slice's mel spectrum is normalised by its own energy (mic gain
    cancels); the two extra values are the log-energy drops attack->body and
    body->tail — how the hit decays. 3 x 24 + 2 = 74 dims.
    """
    import librosa

    fr = _window(audio, sr, t, _MEL_EDGES[-1])
    S = librosa.feature.melspectrogram(
        y=fr, sr=sr, n_fft=1024, hop_length=128, n_mels=_N_MELS, fmin=60, fmax=sr / 2, power=2.0
    )
    hop_s = 128 / sr
    # Floor relative to the hit's own peak so the shape is level-invariant down
    # to (near) digital silence; an absolute floor would leak the gain back in
    # through the quiet tail slice.
    eps = 1e-6 * float(S.max()) + 1e-12
    feats: list[float] = []
    energies: list[float] = []
    for a, b in zip(_MEL_EDGES, _MEL_EDGES[1:]):
        fa, fb = int(a / hop_s), max(int(a / hop_s) + 1, int(b / hop_s))
        sl = S[:, fa:fb].mean(axis=1) + eps
        tot = float(sl.sum())
        feats.extend(float(v) for v in np.log(sl / tot))
        energies.append(float(np.log(tot)))
    feats.append(energies[1] - energies[0])
    feats.append(energies[2] - energies[1])
    return [_safe(v) for v in feats]


def drum_features_v2(audio: np.ndarray, sr: int, t: float) -> list[float]:
    """``all123``: drum10 + mfcc_stats + logmel3."""
    return [*drum_features(audio, sr, t), *mfcc_stats(audio, sr, t), *logmel3(audio, sr, t)]


FEATURES_V2 = (
    FEATURES
    + [f"mfcc{i}_{stat}" for stat in ("mean", "std", "delta") for i in range(13)]
    + [f"mel_{sl}_{b}" for sl in ("attack", "body", "tail") for b in range(_N_MELS)]
    + ["decay_attack_body", "decay_body_tail"]
)

FEATURE_SETS = {
    "drum10": (FEATURES, drum_features),
    "all123": (FEATURES_V2, drum_features_v2),
}
DEFAULT_FEATURE_SET = "all123"


def extract(name: str, audio: np.ndarray, sr: int, t: float) -> list[float]:
    """Feature vector of set ``name`` for the onset at ``t`` (seconds)."""
    return FEATURE_SETS[name][1](audio, sr, t)
