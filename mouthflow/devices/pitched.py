"""Pitched monophonic transcriber for bass and lead voices.

Turns a hummed/sung/voiced single line into pitched MIDI. Where drums need
onset + timbre classification, pitched voices need onset + **pitch**. The
engine is ``librosa.pyin`` (already in the stack — zero new deps, and it
returns per-frame voiced-probability for confidence gating).

Approach: estimate a continuous f0, then **segment** it into notes — rather
than estimating one pitch per onset — because legato singing slides pitch with
no new attack, which a per-onset scheme misses. Boundaries fall at a hard
re-articulation (a detected onset) or a held semitone change. Bass and lead
differ only by a ``VoiceConfig`` (search range, target octave, articulation),
not by algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mouthflow import signal
from mouthflow.schemas import NoteEvent, Transcription

_HOP = 512  # ~11.6 ms at 44.1 kHz — pitch/time resolution


@dataclass(frozen=True)
class VoiceConfig:
    fmin: float                 # pyin search floor (Hz)
    fmax: float                 # pyin search ceiling (Hz)
    target_lo: int              # octave-snap target range, MIDI note (inclusive)
    target_hi: int
    division: int = 8           # quantize grid (8 = 1/8 notes; looser than drums)
    frame_length: int = 2048    # pyin analysis window; larger for low fmin
    min_note_s: float = 0.08    # drop segments shorter than this
    merge_gap_s: float = 0.10   # bridge unvoiced gaps (breaths) up to this long


class PitchedTranscriber:
    def __init__(self, config: VoiceConfig) -> None:
        self.cfg = config

    def transcribe(self, wav_path, *, tempo: float | None = None, bar_align: bool = False) -> Transcription:
        import librosa

        cfg = self.cfg
        y, sr = librosa.load(str(wav_path), sr=signal._SR, mono=True)
        tempo_bpm = float(tempo) if tempo and tempo > 0 else signal.detect_tempo(y, sr)

        f0, voiced_flag, voiced_prob = librosa.pyin(
            y,
            fmin=cfg.fmin,
            fmax=cfg.fmax,
            sr=sr,
            frame_length=cfg.frame_length,
            hop_length=_HOP,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=_HOP)
        rms = librosa.feature.rms(y=y, frame_length=cfg.frame_length, hop_length=_HOP)[0]
        # rms can be 1 frame longer/shorter than f0 depending on padding; align.
        n = min(len(f0), len(rms), len(times))
        f0, voiced_flag, voiced_prob, times, rms = (
            f0[:n], voiced_flag[:n], voiced_prob[:n], times[:n], rms[:n]
        )

        stones = self._frame_semitones(f0, voiced_flag, voiced_prob, rms)
        merge_gap_frames = max(1, int(cfg.merge_gap_s * sr / _HOP))

        segments = self._segment(stones, merge_gap_frames)
        hits = self._segments_to_notes(segments, times, rms, tempo_bpm, merge_gap_frames)

        bars = len(y) / sr * (tempo_bpm / 60.0) / 4.0

        import tempfile
        from pathlib import Path

        midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
        signal.write_midi(midi_path, hits, tempo_bpm, channel=0)

        return Transcription(
            midi_path=midi_path,
            tempo_bpm=float(tempo_bpm),
            bars=float(bars),
            hits=hits,
        )

    # --- stages ---

    def _frame_semitones(self, f0, voiced_flag, voiced_prob, rms) -> list[int | None]:
        """Per-frame rounded semitone (or None for unvoiced/quiet frames).

        Median-smooth the voiced f0 (~50 ms) to kill vibrato/scoops before
        rounding, so a steady note doesn't flip between two semitones.
        """
        import librosa

        floor = 0.005
        midi = librosa.hz_to_midi(np.where(np.isnan(f0), 1.0, f0))  # 1 Hz placeholder
        voiced = voiced_flag & ~np.isnan(f0) & (np.nan_to_num(voiced_prob) >= 0.5) & (rms >= floor)

        # Median smooth over voiced frames only.
        win = 5  # ~58 ms
        smoothed = midi.copy()
        half = win // 2
        for i in range(len(midi)):
            lo, hi = max(0, i - half), min(len(midi), i + half + 1)
            window = midi[lo:hi][voiced[lo:hi]]
            if window.size:
                smoothed[i] = np.median(window)

        return [int(round(smoothed[i])) if voiced[i] else None for i in range(len(midi))]

    def _segment(self, stones, merge_gap_frames):
        """Group consecutive same-semitone voiced frames into note segments.

        A segment closes on a held semitone change or a long unvoiced gap.
        Onsets are deliberately NOT used to split: ``onset_detect`` fires
        spuriously on a sustained tone, which would shatter one held note into
        many fragments. Re-articulated same-pitch notes are instead separated
        by the silence between them (the gap rule) or merged downstream.
        """
        segments: list[dict] = []
        cur: dict | None = None
        gap = 0

        def close(seg):
            if seg is not None:
                segments.append(seg)

        for i, stone in enumerate(stones):
            if stone is not None:
                if cur is None:
                    cur = {"start": i, "end": i, "pitches": [stone]}
                elif stone != _mode(cur["pitches"]):
                    close(cur)
                    cur = {"start": i, "end": i, "pitches": [stone]}
                else:
                    cur["end"] = i
                    cur["pitches"].append(stone)
                gap = 0
            else:
                if cur is not None:
                    gap += 1
                    if gap > merge_gap_frames:
                        close(cur)
                        cur = None
                        gap = 0
        close(cur)
        return segments

    def _segments_to_notes(self, segments, times, rms, tempo_bpm, merge_gap_frames):
        cfg = self.cfg

        # Snap each segment's pitch, then merge adjacent same-pitch segments
        # split only by a single-frame median-smoothing flip or a tiny gap.
        snapped: list[list] = []  # [pitch, start_i, end_i]
        for seg in segments:
            pitch = _snap_octave(_mode(seg["pitches"]), cfg.target_lo, cfg.target_hi)
            if snapped and snapped[-1][0] == pitch and seg["start"] - snapped[-1][2] <= merge_gap_frames:
                snapped[-1][2] = seg["end"]
            else:
                snapped.append([pitch, seg["start"], seg["end"]])

        notes: list[NoteEvent] = []
        for pitch, start_i, end_i in snapped:
            start_t = float(times[start_i])
            # Extend to the next frame's time so a 1-frame note still has a real
            # duration; clamp inside the array.
            end_t = float(times[min(end_i + 1, len(times) - 1)])
            dur = end_t - start_t
            if dur < cfg.min_note_s:
                continue
            seg_rms = float(np.mean(rms[start_i : end_i + 1]))
            velocity = signal.velocity_from_rms(seg_rms)
            start_q = signal.quantise(start_t, tempo_bpm, division=cfg.division)
            notes.append(
                NoteEvent(
                    time_s=start_q,
                    midi_note=int(pitch),
                    velocity=velocity,
                    duration_s=float(dur),
                )
            )
        notes.sort(key=lambda nt: (nt.time_s, nt.midi_note))
        return notes


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi_note: int) -> str:
    """Scientific pitch name (C4 = MIDI 60)."""
    return f"{_NOTE_NAMES[midi_note % 12]}{midi_note // 12 - 1}"


def pitched_plan_summary(t: Transcription) -> dict:
    """Transcription summary for the planner in a pitched vocabulary: how many
    notes and what register, so it can pick a fitting bass/lead instrument."""
    if not t.hits:
        return {"note_count": 0, "pitch_range": None}
    pitches = [h.midi_note for h in t.hits]
    lo, hi = min(pitches), max(pitches)
    return {
        "note_count": len(t.hits),
        "pitch_range": [_note_name(lo), _note_name(hi)],
        "lowest_midi": lo,
        "highest_midi": hi,
    }


def _mode(values: list[int]) -> int:
    """Most common value (ties → smallest), robust to vibrato wobble."""
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    return best[0]


def _snap_octave(midi_note: int, lo: int, hi: int) -> int:
    """Shift by octaves into [lo, hi] — fixes hummed-an-octave-off lines."""
    while midi_note < lo:
        midi_note += 12
    while midi_note > hi:
        midi_note -= 12
    return midi_note
