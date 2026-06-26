"""Drum transcriber: beatbox WAV -> GM drum MIDI + tempo.

The ``PERCUSSIVE`` clip mode in action — onset detection, per-onset timbre
classification (``classify._classify``), 16th-note quantization, and a GM
channel-9 MIDI write. All generic DSP comes from ``mouthflow.signal``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from mouthflow import signal
from mouthflow.devices.drum.classify import DROP, _classify
from mouthflow.schemas import DrumHit, Transcription


class DrumTranscriber:
    def transcribe(self, wav_path: Path) -> Transcription:
        import librosa

        y, sr = librosa.load(str(wav_path), sr=signal._SR, mono=True)

        tempo_bpm = signal.detect_tempo(y, sr)
        onset_times = signal.detect_onsets(y, sr)

        hits: list[DrumHit] = []
        for t in onset_times:
            features = signal.features_at(y, sr, t)
            note = _classify(features)
            if note == DROP:
                continue
            velocity = signal.velocity_from_rms(features["rms"])
            t_quantised = signal.quantise(t, tempo_bpm, division=16)
            hits.append(DrumHit(time_s=t_quantised, midi_note=note, velocity=velocity))

        bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

        midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
        signal.write_midi(midi_path, hits, tempo_bpm, channel=9)

        return Transcription(
            midi_path=midi_path,
            tempo_bpm=float(tempo_bpm),
            bars=float(bars),
            hits=hits,
        )
