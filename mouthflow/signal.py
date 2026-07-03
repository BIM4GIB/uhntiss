"""Shared, voice-agnostic DSP primitives.

Extracted from ``transcribe.py`` so every device (drums, bass, lead, drone)
draws onset/tempo/feature/quantize/MIDI helpers from one place. Nothing here
knows about drums, pitch classes, or GM note maps — those policies live in the
per-device transcribers under ``mouthflow/devices/``.

``transcribe.py`` re-exports the names it historically owned (``_SR``,
``_detect_onsets``, ``_features_at``, ``_quantise_16th``, ``_write_midi`` …) so
existing callers (``eval/``, ``mimic/``, the tests) keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import mido
import numpy as np

_SR = 44_100
_WINDOW_S = 0.120

# Onset peak-picking. Broadband envelope + default hop keep low mouth-kicks
# (an HF-emphasised envelope drops them). delta slightly above librosa's 0.07
# trims marginal false onsets; the wait floor suppresses double-triggers on a
# single transient — both well under a 16th note at any beatbox tempo.
_ONSET_DELTA = 0.10
_ONSET_WAIT_S = 0.040


def detect_tempo(y: np.ndarray, sr: int) -> float:
    """Simple beat-track tempo (used by the pitched/drone voices for sizing).

    The drum device uses its own octave-correct estimator — see
    ``mouthflow.devices.drum.tempo``.
    """
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo).item() if np.ndim(tempo) > 0 else tempo)
    if tempo <= 0:
        tempo = 120.0
    return tempo


def detect_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    frames = librosa.onset.onset_detect(
        y=y,
        sr=sr,
        backtrack=True,
        units="frames",
        delta=_ONSET_DELTA,
        wait=max(1, int(_ONSET_WAIT_S * sr / 512)),  # 512 = librosa default hop
    )
    return librosa.frames_to_time(frames, sr=sr)


def features_at(y: np.ndarray, sr: int, t: float) -> dict[str, float]:
    """Timbre feature vector for the ~120 ms window starting at time ``t``.

    Voice-neutral DSP: spectral centroid, flatness, ZCR, RMS, sub-100 Hz energy
    ratio, and decay. Drums classify on these; the drone contour extractor will
    reuse centroid/RMS. Pitch (for bass/lead) is computed separately via pyin.
    """
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


def velocity_from_rms(rms: float) -> int:
    # Map rms in [0.01, 0.3] logarithmically to [40, 120], clamp.
    # Absolute (mic-gain-dependent) — prefer ``velocities_from_rms`` for a
    # whole take, which normalises per-performance.
    if rms <= 0:
        return 40
    db = 20 * np.log10(max(rms, 1e-4))
    # -40 dB -> 40, -10 dB -> 120.
    vel = 40 + (db - (-40)) * (120 - 40) / 30
    return int(np.clip(vel, 1, 127))


def velocities_from_rms(values: list[float]) -> list[int]:
    """Per-take velocity mapping: the TAKE's dynamics set the range, not the
    absolute mic level.

    The take's median loudness lands at ~90; spread is measured in dB
    (p10–p90) and the output range scales with it — a genuinely dynamic take
    reaches ghost notes (~35) and accents (~126), a deliberately flat take
    stays flat instead of having noise amplified into fake dynamics. Short
    takes (< 4 events) fall back to the absolute map.
    """
    if not values:
        return []
    if len(values) < 4:
        return [velocity_from_rms(v) for v in values]
    db = np.array([20 * np.log10(max(float(v), 1e-4)) for v in values])
    med = float(np.median(db))
    p10, p90 = (float(x) for x in np.percentile(db, [10, 90]))
    spread = p90 - p10
    if spread < 1.0:
        return [90] * len(values)  # essentially flat performance — keep it flat
    scale = min(1.0, spread / 12.0)  # full range only for truly dynamic takes
    # Piecewise-linear percentile anchors: p10 -> ghost (45), median -> 90,
    # p90 -> accent (120), extrapolated beyond and clipped. Denominators are
    # floored so a skewed take can't divide by ~0.
    lo_den = max(med - p10, spread / 4.0)
    hi_den = max(p90 - med, spread / 4.0)
    out = []
    for d in db:
        if d <= med:
            v = 90 + (d - med) / lo_den * 45.0 * scale
        else:
            v = 90 + (d - med) / hi_den * 30.0 * scale
        out.append(int(np.clip(round(v), 20, 127)))
    return out


def quantise(t_s: float, tempo_bpm: float, division: int = 16) -> float:
    """Snap ``t_s`` to the nearest 1/``division`` note at ``tempo_bpm``.

    ``division=16`` (the default, and the drum path's behaviour) snaps to 16th
    notes. Pitched devices pass a looser grid; drone skips quantization.
    """
    step = (60.0 / tempo_bpm) * (4.0 / division)
    return round(t_s / step) * step


def write_midi(
    path: Path,
    notes,
    tempo_bpm: float,
    *,
    channel: int = 0,
    default_dur_ticks: int | None = None,
    tpb: int = 480,
) -> None:
    """Write ``notes`` (anything with ``time_s``/``midi_note``/``velocity``,
    optionally ``duration_s``) to a MIDI file.

    Per-note duration policy: an explicit ``duration_s`` wins (pitched + drone
    sustains); else ``default_dur_ticks`` if given; else a fixed 1/32 note
    (``tpb // 8``) — the drum default. ``channel`` is 9 for GM drums, 0 for
    pitched instruments.
    """
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))

    events: list[tuple[int, str, int, int]] = []
    for n in notes:
        tick = int(round(n.time_s * tempo_bpm / 60.0 * tpb))
        dur_s = getattr(n, "duration_s", None)
        if dur_s is not None:
            dur = max(1, int(round(dur_s * tempo_bpm / 60.0 * tpb)))
        elif default_dur_ticks is not None:
            dur = default_dur_ticks
        else:
            dur = tpb // 8  # 1/32-note duration (drum default)
        events.append((tick, "on", n.midi_note, n.velocity))
        events.append((tick + dur, "off", n.midi_note, 0))
    events.sort()

    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta, channel=channel))

    mid.save(path)
