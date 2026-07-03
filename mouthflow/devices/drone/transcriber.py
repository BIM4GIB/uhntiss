"""Drone transcriber: a sustained hum -> long held note(s) / a held chord.

The odd voice out — not about rhythmic onsets at all. We find *stable pitch
regions* over the whole clip: one dominant region becomes a single long held
note; several regions (a voice can only sing one note at a time, so a chord
must be hummed as a sequence) become notes that each enter at their region and
all sustain to the clip end, ringing together as a held chord.

Clip length is snapped up to a whole bar; the held notes fill it. In Live the
clip loops, so a few seconds of hum become a continuous evolving drone — the
*movement* comes from the chosen pad preset (slow attack, LFOs), not from us.

This is the ``SUSTAINED`` clip mode. ``execute.py`` needs no changes: the held
chord flows through ``_midi_to_notes`` unchanged. The pitch/loudness contour ->
automation layer (which DOES need new bridge surface) lives in ``contour.py``.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mouthflow import signal
from mouthflow.schemas import NoteEvent, Transcription

_HOP = 512


@dataclass(frozen=True)
class DroneConfig:
    fmin: float = 65.0      # ~C2
    fmax: float = 1050.0    # ~C6
    frame_length: int = 4096
    pitch_tol: int = 1      # semitones — within this stays one region
    min_region_s: float = 0.30
    merge_gap_s: float = 0.15  # bridge breaths within a held tone
    rms_floor: float = 0.005


class DroneTranscriber:
    def __init__(self, config: DroneConfig | None = None) -> None:
        self.cfg = config or DroneConfig()

    def transcribe(self, wav_path, *, tempo: float | None = None, bar_align: bool = False) -> Transcription:
        import librosa

        cfg = self.cfg
        y, sr = librosa.load(str(wav_path), sr=signal._SR, mono=True)
        tempo_bpm = float(tempo) if tempo and tempo > 0 else signal.detect_tempo(y, sr)

        f0, voiced_flag, voiced_prob = librosa.pyin(
            y, fmin=cfg.fmin, fmax=cfg.fmax, sr=sr,
            frame_length=cfg.frame_length, hop_length=_HOP,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=_HOP)
        rms = librosa.feature.rms(y=y, frame_length=cfg.frame_length, hop_length=_HOP)[0]
        n = min(len(f0), len(rms), len(times))
        f0, voiced_flag, voiced_prob, times, rms = (
            f0[:n], voiced_flag[:n], voiced_prob[:n], times[:n], rms[:n]
        )

        voiced = (
            voiced_flag & ~np.isnan(f0)
            & (np.nan_to_num(voiced_prob) >= 0.5)
            & (rms >= cfg.rms_floor)
        )
        semis = np.where(voiced, np.round(librosa.hz_to_midi(np.where(np.isnan(f0), 1.0, f0))), np.nan)

        regions = self._regions(semis, voiced, times, rms)

        # Clip end snapped up to a whole bar; the held notes fill it.
        total_dur = len(y) / sr
        bar_s = 4.0 * 60.0 / tempo_bpm
        clip_bars = max(1, math.ceil(total_dur / bar_s)) if bar_s > 0 else 1
        clip_end = clip_bars * bar_s

        vprob = np.nan_to_num(voiced_prob)
        hits: list[NoteEvent] = []
        for reg in regions:
            start_t = float(times[reg["start"]])
            dur = max(clip_end - start_t, cfg.min_region_s)
            seg_rms = float(np.mean(rms[reg["start"] : reg["end"] + 1]))
            # Record pyin's voiced-probability for diagnostics and future
            # gating (e.g. dropping phantom chord tones). Note SUSTAINED clips
            # are never scale-snapped, so refine's keep-confident rule does
            # not consume this today.
            conf = float(np.mean(vprob[reg["start"] : reg["end"] + 1]))
            hits.append(
                NoteEvent(
                    time_s=start_t,
                    midi_note=int(reg["pitch"]),
                    velocity=signal.velocity_from_rms(seg_rms),
                    duration_s=dur,
                    confidence=round(conf, 3),
                )
            )
        hits.sort(key=lambda nt: (nt.time_s, nt.midi_note))

        midi_path = Path(tempfile.mkstemp(suffix=".mid", prefix="mouthflow_")[1])
        signal.write_midi(midi_path, hits, tempo_bpm, channel=0)

        # Loudness contour -> macro automation (rendered when the forked bridge
        # exposes set_clip_envelope; otherwise apply_plan skips it and the drone
        # still plays as a held note/chord).
        from mouthflow.devices.drone.contour import extract_loudness_contour

        automation = [extract_loudness_contour(y, sr, tempo_bpm)] if hits else []

        return Transcription(
            midi_path=midi_path,
            tempo_bpm=float(tempo_bpm),
            bars=float(clip_bars),
            hits=hits,
            automation=automation,
        )

    def _regions(self, semis, voiced, times, rms):
        """Merge voiced frames into stable pitch regions (±pitch_tol)."""
        cfg = self.cfg
        merge_gap_frames = max(1, int(cfg.merge_gap_s * (signal._SR / _HOP)))
        regions: list[dict] = []
        cur: dict | None = None
        gap = 0

        def close(seg):
            if seg is None:
                return
            dur = float(times[min(seg["end"] + 1, len(times) - 1)]) - float(times[seg["start"]])
            if dur >= cfg.min_region_s:
                seg["pitch"] = _mode(seg["pitches"])
                regions.append(seg)

        for i in range(len(semis)):
            if voiced[i]:
                s = int(semis[i])
                if cur is None:
                    cur = {"start": i, "end": i, "pitches": [s]}
                elif abs(s - _mode(cur["pitches"])) <= cfg.pitch_tol:
                    cur["end"] = i
                    cur["pitches"].append(s)
                else:
                    close(cur)
                    cur = {"start": i, "end": i, "pitches": [s]}
                gap = 0
            else:
                if cur is not None:
                    gap += 1
                    if gap > merge_gap_frames:
                        close(cur)
                        cur = None
                        gap = 0
        close(cur)
        return regions


def drone_plan_summary(t: Transcription) -> dict:
    """Pad/character summary: held pitches and whether it's a chord."""
    from mouthflow.devices.pitched import _note_name

    if not t.hits:
        return {"voice_count": 0, "is_chord": False, "pitches": []}
    pitches = sorted(h.midi_note for h in t.hits)
    return {
        "voice_count": len(t.hits),
        "is_chord": len(t.hits) > 1,
        "pitches": [_note_name(p) for p in pitches],
        "clip_bars": round(t.bars, 2),
    }


def _mode(values: list[int]) -> int:
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
