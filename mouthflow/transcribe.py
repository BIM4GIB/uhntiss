"""Beatbox WAV -> drum MIDI + tempo.

v0.1 pipeline (spec §component 3):

1. Onset detection via librosa.onset.onset_detect(backtrack=True).
2. Per-onset 120ms window feature vector: spectral centroid, spectral
   flatness, zero-crossing rate, RMS, sub-100Hz energy ratio.
3. Hand-tuned heuristic classifier → GM drum note.
4. librosa.beat.beat_track for tempo.
5. Quantise onsets to 16th notes at the detected tempo.
6. Write MIDI via mido (GM drum map, channel 10 = MIDI channel 9).

Thresholds are sensible defaults. The 20-clip corpus is what tunes them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import librosa
import mido
import numpy as np

from mouthflow.schemas import DrumHit, Transcription

GM_KICK = 36
GM_SNARE = 38
GM_HAT_CLOSED = 42
GM_HAT_OPEN = 46
GM_PERC = 39  # unused in v0.1 but reserved

DROP = -1  # sentinel returned by classify when we'd rather silence than guess

_WINDOW_S = 0.120
_SR = 44_100


def transcribe_drums(wav_path: Path) -> Transcription:
    y, sr = librosa.load(str(wav_path), sr=_SR, mono=True)

    tempo_bpm = _detect_tempo(y, sr)
    onset_times = _detect_onsets(y, sr)

    hits: list[DrumHit] = []
    for t in onset_times:
        features = _features_at(y, sr, t)
        note = _classify(features)
        if note == DROP:
            continue
        velocity = _velocity_from_rms(features["rms"])
        t_quantised = _quantise_16th(t, tempo_bpm)
        hits.append(DrumHit(time_s=t_quantised, midi_note=note, velocity=velocity))

    bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

    midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
    _write_midi(midi_path, hits, tempo_bpm)

    return Transcription(
        midi_path=midi_path,
        tempo_bpm=float(tempo_bpm),
        bars=float(bars),
        hits=hits,
    )


# --- stages ---


def _detect_tempo(y: np.ndarray, sr: int) -> float:
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo).item() if np.ndim(tempo) > 0 else tempo)
    if tempo <= 0:
        tempo = 120.0
    return tempo


def _detect_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True, units="frames")
    return librosa.frames_to_time(frames, sr=sr)


def _features_at(y: np.ndarray, sr: int, t: float) -> dict[str, float]:
    start = int(t * sr)
    end = min(start + int(_WINDOW_S * sr), len(y))
    frame = y[start:end]
    if len(frame) < 64:
        return {
            "centroid": 0.0,
            "flatness": 0.0,
            "zcr": 0.0,
            "rms": 0.0,
            "sub100_ratio": 0.0,
            "decay_s": 0.0,
        }

    # n_fft capped to frame length (librosa warns otherwise).
    n_fft = min(1024, 1 << (len(frame) - 1).bit_length())

    centroid = float(librosa.feature.spectral_centroid(y=frame, sr=sr, n_fft=n_fft).mean())
    flatness = float(librosa.feature.spectral_flatness(y=frame, n_fft=n_fft).mean())
    zcr = float(librosa.feature.zero_crossing_rate(y=frame).mean())
    rms = float(np.sqrt(np.mean(frame**2)))

    spec = np.abs(np.fft.rfft(frame, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, d=1 / sr)
    total = spec.sum() + 1e-9
    sub100_ratio = float(spec[freqs < 100].sum() / total)

    # Decay: time from peak RMS to -12dB, computed in 10ms hops.
    hop = max(1, int(0.010 * sr))
    rms_env = np.array([np.sqrt(np.mean(frame[i : i + hop] ** 2)) for i in range(0, len(frame) - hop, hop)])
    if rms_env.size > 1 and rms_env.max() > 0:
        peak = rms_env.argmax()
        threshold = rms_env.max() * 0.25  # -12 dB
        tail = rms_env[peak:]
        below = np.where(tail < threshold)[0]
        decay_s = (below[0] if below.size else len(tail)) * hop / sr
    else:
        decay_s = 0.0

    return {
        "centroid": centroid,
        "flatness": flatness,
        "zcr": zcr,
        "rms": rms,
        "sub100_ratio": sub100_ratio,
        "decay_s": decay_s,
    }


def _classify(f: dict[str, float]) -> int:
    """Heuristic drum classifier. Returns a GM pitch or DROP.

    Thresholds are sensible defaults; the 20-clip corpus tunes them.
    Ordering: kick (sub-bass dominant) > hat (very high centroid) > snare
    (mid band) > drop.
    """
    centroid = f["centroid"]
    sub100 = f["sub100_ratio"]
    decay = f["decay_s"]
    rms = f["rms"]

    if rms < 0.01:
        return DROP

    if sub100 > 0.25 or (centroid < 1200 and sub100 > 0.10):
        return GM_KICK
    if centroid > 5000:
        return GM_HAT_OPEN if decay > 0.060 else GM_HAT_CLOSED
    if 1200 <= centroid <= 5000:
        return GM_SNARE
    return DROP


def _velocity_from_rms(rms: float) -> int:
    # Map rms ∈ [0.01, 0.3] logarithmically to [40, 120], clamp.
    if rms <= 0:
        return 40
    db = 20 * np.log10(max(rms, 1e-4))
    # -40 dB -> 40, -10 dB -> 120.
    vel = 40 + (db - (-40)) * (120 - 40) / 30
    return int(np.clip(vel, 1, 127))


def _quantise_16th(t_s: float, tempo_bpm: float) -> float:
    step = 60.0 / tempo_bpm / 4.0
    return round(t_s / step) * step


def _write_midi(path: Path, hits: list[DrumHit], tempo_bpm: float) -> None:
    tpb = 480
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))

    events: list[tuple[int, str, int, int]] = []
    for hit in hits:
        tick = int(round(hit.time_s * tempo_bpm / 60.0 * tpb))
        events.append((tick, "on", hit.midi_note, hit.velocity))
        events.append((tick + tpb // 8, "off", hit.midi_note, 0))  # 1/32-note duration
    events.sort()

    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta, channel=9))

    mid.save(path)
