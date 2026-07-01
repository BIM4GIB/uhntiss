"""Beatbox WAV -> drum MIDI + tempo (back-compat facade).

The drum pipeline now lives under ``mouthflow.devices.drum`` (transcriber +
classifier + beatbox tempo) over the shared DSP in ``mouthflow.signal``. This
module stays a thin facade that re-exports the historic public names so existing
callers — ``eval/run_eval.py``, ``eval/onset_sanity.py``,
``eval/train_classifier.py``, ``mimic/take.py`` and the tests — keep importing
them from ``mouthflow.transcribe`` unchanged.

Note: the drum model lives in ``devices.drum.classify`` and is read at call
time. To force the heuristic in a test, patch ``_MODEL`` on *that* module
(``mouthflow.devices.drum.classify``), not here.
"""

from __future__ import annotations

from pathlib import Path

from mouthflow import signal
from mouthflow.devices.drum import tempo as _drum_tempo
from mouthflow.devices.drum.classify import (  # noqa: F401  (re-exported)
    DROP,
    GM_HAT_CLOSED,
    GM_HAT_OPEN,
    GM_KICK,
    GM_PERC,
    GM_SNARE,
    _classify,
    _classify_heuristic,
    _load_model,
)
from mouthflow.devices.drum.transcriber import DrumTranscriber
from mouthflow.schemas import DrumHit, Transcription  # noqa: F401  (re-exported)

# --- shared-DSP re-exports (back-compat for eval/, mimic/, tests) ---
_SR = signal._SR
_WINDOW_S = signal._WINDOW_S
_detect_onsets = signal.detect_onsets
_features_at = signal.features_at
_velocity_from_rms = signal.velocity_from_rms

# --- beatbox tempo / grid re-exports ---
_detect_tempo = _drum_tempo._detect_tempo
_refine_tempo = _drum_tempo._refine_tempo
_grid_phase = _drum_tempo._grid_phase
_quantise_grid = _drum_tempo._quantise_grid


def _quantise_16th(t_s: float, tempo_bpm: float) -> float:
    """Back-compat alias: snap to 16th notes via ``signal.quantise``."""
    return signal.quantise(t_s, tempo_bpm, division=16)


def _write_midi(path: Path, hits: list[DrumHit], tempo_bpm: float) -> None:
    """Back-compat alias: GM drum write (channel 9, 1/32-note durations)."""
    signal.write_midi(path, hits, tempo_bpm, channel=9)


def transcribe_drums(
    wav_path: Path, tempo: float | None = None, bar_align: bool = False
) -> Transcription:
    """Beatbox WAV -> GM drum ``Transcription`` (the drums device).

    ``tempo`` forces the BPM (skips detection); otherwise the drum device's
    octave-correct estimator runs. ``bar_align`` snaps to the bar grid (phase 0)
    for a tighter project fit instead of the performer's lead-in phase.
    """
    return DrumTranscriber().transcribe(wav_path, tempo=tempo, bar_align=bar_align)
