"""Drum transcriber: beatbox WAV -> GM drum MIDI + tempo.

The ``PERCUSSIVE`` clip mode: onset detection, per-onset timbre classification
(``classify._classify``), beatbox-tuned octave-correct tempo with
confidence-gated, phase-aware 16th-note quantization (``tempo``), and a GM
channel-9 MIDI write. Generic DSP comes from ``mouthflow.signal``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from mouthflow import signal
from mouthflow.devices.drum import tempo as drum_tempo
from mouthflow.devices.drum.classify import DROP, _classify
from mouthflow.schemas import DrumHit, Transcription


class DrumTranscriber:
    def transcribe(self, wav_path: Path, *, tempo: float | None = None) -> Transcription:
        import librosa

        y, sr = librosa.load(str(wav_path), sr=signal._SR, mono=True)

        # Detect + classify every onset first; the kept (non-DROP) onsets are
        # the signal the tempo/phase estimator fits — noise would pollute it.
        kept: list[tuple[float, int, int]] = []  # (time_s, midi_note, velocity)
        for t in signal.detect_onsets(y, sr):
            features = signal.features_at(y, sr, t)
            note = _classify(y, sr, t, features)
            if note == DROP:
                continue
            kept.append((float(t), note, signal.velocity_from_rms(features["rms"])))

        kept_times = np.array([t for t, _, _ in kept], dtype=float)

        if tempo is not None and tempo > 0:
            tempo_bpm, confidence = float(tempo), 1.0  # explicit tempo trusted as-is
        else:
            tempo_bpm, confidence = drum_tempo._detect_tempo(y, sr, kept_times)

        # Quantise only when the tempo is trustworthy. Snapping to a *wrong*
        # tempo (or wrong grid phase) shears every hit off the played timing and
        # tanks onset F1; raw onsets are the safe fallback.
        trust_tempo = confidence >= drum_tempo._QUANT_CONF_MIN
        phase = 0.0
        if trust_tempo:
            if tempo is None:
                # Sharpen the octave-correct estimate to sub-BPM.
                tempo_bpm = drum_tempo._refine_tempo(kept_times, tempo_bpm)
            phase = drum_tempo._grid_phase(kept_times, tempo_bpm)

        hits: list[DrumHit] = []
        seen: set[tuple[int, int]] = set()
        for t, note, velocity in kept:
            if trust_tempo:
                t_out = drum_tempo._quantise_grid(t, tempo_bpm, phase)
                # Two onsets that collapse onto one grid slot + pitch are one hit.
                key = (round(t_out * 1000), note)
                if key in seen:
                    continue
                seen.add(key)
            else:
                t_out = t
            hits.append(DrumHit(time_s=t_out, midi_note=note, velocity=velocity))

        bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

        midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
        signal.write_midi(midi_path, hits, tempo_bpm, channel=9)

        return Transcription(
            midi_path=midi_path,
            tempo_bpm=float(tempo_bpm),
            bars=float(bars),
            hits=hits,
        )
