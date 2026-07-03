"""Intent routing for a captured clip — which voice is this?

A lightweight heuristic over two cues: how *voiced/tonal* the clip is (pyin
voiced fraction) and how *stable* its pitch is. Drums are mostly unvoiced;
a drone holds one near-constant pitch; bass and lead are voiced with a moving
pitch, split by register.

Note: ``onset_detect`` fires spuriously on sustained tones, so onset density is
deliberately NOT used to tell drone from melody — pitch stability is the
reliable cue. A hummed *chord* drone (a moving pitch) routes to a pitched voice;
select ``--device drone`` explicitly for that case.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mouthflow import signal
from mouthflow.schemas import Intent

# Register boundary between bass and lead (E3 = MIDI 52).
_BASS_CEILING = 52

# Routing needs the CHARACTER of the take, not all of it: voiced-fraction and
# pitch stability are established within a few seconds, and pyin over a full
# take costs seconds that the device transcriber then re-spends. Analyse a
# window (skipping is fine — the router's verdict picks which transcriber
# runs on the FULL audio).
_ROUTER_WINDOW_S = 6.0


def classify(wav_path: Path) -> tuple[Intent, float]:
    import librosa

    y, sr = librosa.load(str(wav_path), sr=signal._SR, mono=True, duration=_ROUTER_WINDOW_S)
    if y.size == 0:
        return (Intent.UNKNOWN, 0.0)

    f0, voiced_flag, voiced_prob = librosa.pyin(
        y, fmin=65.0, fmax=1000.0, sr=sr, frame_length=2048, hop_length=512
    )
    voiced = voiced_flag & ~np.isnan(f0) & (np.nan_to_num(voiced_prob) >= 0.5)
    voiced_frac = float(np.mean(voiced)) if voiced.size else 0.0

    if voiced.any():
        midi = librosa.hz_to_midi(f0[voiced])
        pitch_std = float(np.std(midi))
        median_pitch = float(np.median(midi))
    else:
        pitch_std, median_pitch = 99.0, 0.0

    # Mostly unvoiced / percussive -> drums.
    if voiced_frac < 0.4:
        return (Intent.DRUM, round(0.5 + (0.4 - voiced_frac), 2))

    # Voiced and near-constant pitch -> a held drone.
    if pitch_std < 1.0:
        return (Intent.DRONE, 0.7)

    # Voiced and moving -> a pitched line, split by register.
    if median_pitch and median_pitch < _BASS_CEILING:
        return (Intent.BASS, 0.6)
    return (Intent.MELODY, 0.6)
