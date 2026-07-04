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

from dataclasses import dataclass, replace

import numpy as np

from mouthflow import signal
from mouthflow.schemas import NoteEvent, Transcription

_HOP = 512  # ~11.6 ms at 44.1 kHz — pitch/time resolution

# Snap note starts to the grid only when the performance actually sits on it
# (mean best-phase distance in steps; ~0.25 = unrelated grid). Above this,
# performed timing is kept raw — snapping to an alien grid shears the line.
_GRID_TRUST_MAX = 0.20


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
    min_stable_s: float = 0.08  # a pitch change must hold this long to start a new
    #                             note — absorbs vibrato wobble + glide pass-through
    min_confidence: float = 0.2  # drop notes whose mean pyin voiced-prob is below
    #                              this (spurious attack/breath blips)


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

        # Articulation-resolution RMS (23ms window) for the gap-continuity
        # check: the pitch-window RMS above (e.g. 93ms for bass) smears right
        # across a staccato gap, so an 80ms silence never reads as silent
        # through it.
        art_rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=_HOP)[0][:n]
        segments = self._segment(stones, merge_gap_frames, art_rms)
        hits = self._segments_to_notes(segments, times, rms, voiced_prob, tempo_bpm, merge_gap_frames)

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

    def _segment(self, stones, merge_gap_frames, rms):
        """Group consecutive same-semitone voiced frames into note segments.

        A segment closes on a *held* semitone change, a long unvoiced gap, or
        a short gap through which the LEVEL dropped (a re-articulation: a
        pumping same-pitch 8th line dips to near-silence between notes, while
        pyin flicker during a genuinely held tone keeps its RMS up — verified
        on a real take where gap-bridging fused ~36 hummed notes into 20).
        A pitch change must persist >= ``min_stable`` frames to split a note —
        a momentary excursion (a vibrato peak, or a glide passing through a
        semitone on its way somewhere) is absorbed into the current note
        instead of spawning a spurious fragment. Onsets are deliberately NOT
        used to split: ``onset_detect`` fires on a sustained tone, which would
        shatter one held note.
        """
        min_stable = max(2, int(self.cfg.min_stable_s * (signal._SR / _HOP)))
        segments: list[dict] = []
        cur: dict | None = None
        gap = 0
        pend_pitch: int | None = None  # a candidate new pitch, not yet committed
        pend_start = 0
        pend_count = 0

        def close(seg):
            if seg is not None:
                segments.append(seg)

        for i, stone in enumerate(stones):
            if stone is None:
                if cur is not None:
                    gap += 1
                    if gap > merge_gap_frames:
                        close(cur)
                        cur = None
                        gap = 0
                        pend_pitch = None
                        pend_count = 0
                continue
            if cur is not None and gap > 0 and not _gap_continuous(rms, i - gap - 1, i):
                # Bridged gap, but the sound stopped in it -> re-articulation.
                close(cur)
                cur = None
                pend_pitch = None
                pend_count = 0
            gap = 0
            if cur is None:
                cur = {"start": i, "end": i, "pitches": [stone]}
                pend_pitch = None
                pend_count = 0
            elif stone == _mode(cur["pitches"]):
                cur["end"] = i
                cur["pitches"].append(stone)
                pend_pitch = None
                pend_count = 0
            else:
                # different semitone: count how long it persists before trusting it
                if stone == pend_pitch:
                    pend_count += 1
                else:
                    pend_pitch = stone
                    pend_start = i
                    pend_count = 1
                if pend_count >= min_stable:
                    cur["end"] = pend_start - 1  # the new note owns its run
                    close(cur)
                    cur = {"start": pend_start, "end": i, "pitches": [stone] * pend_count}
                    pend_pitch = None
                    pend_count = 0
                else:
                    cur["end"] = i  # absorb the excursion; mode stays put
        close(cur)
        return segments

    def _segments_to_notes(self, segments, times, rms, voiced_prob, tempo_bpm, merge_gap_frames):
        cfg = self.cfg
        vprob = np.nan_to_num(voiced_prob)

        # Snap each segment's pitch, then merge adjacent same-pitch segments
        # split only by a single-frame median-smoothing flip or a tiny gap —
        # but ONLY when the sound actually continued through the gap. A real
        # re-articulation dips to near-silence between notes; merging those
        # collapsed a pumping same-pitch 8th line into one held note
        # (verified on a real take: ~36 hummed notes came out as 20). pyin
        # flicker during a held tone keeps its RMS up, so it still bridges.
        snapped: list[list] = []  # [pitch, start_i, end_i]
        for seg in segments:
            pitch = _snap_octave(_mode(seg["pitches"]), cfg.target_lo, cfg.target_hi)
            if (
                snapped
                and snapped[-1][0] == pitch
                and seg["start"] - snapped[-1][2] <= merge_gap_frames
                and _gap_continuous(rms, snapped[-1][2], seg["start"])
            ):
                snapped[-1][2] = seg["end"]
            else:
                snapped.append([pitch, seg["start"], seg["end"]])

        accepted: list[tuple[int, float, float, float, float]] = []  # pitch, t_raw, dur, conf, rms
        for pitch, start_i, end_i in snapped:
            start_t = float(times[start_i])
            # Extend to the next frame's time so a 1-frame note still has a real
            # duration; clamp inside the array.
            end_t = float(times[min(end_i + 1, len(times) - 1)])
            dur = end_t - start_t
            if dur < cfg.min_note_s:
                continue
            conf = float(np.mean(vprob[start_i : end_i + 1])) if end_i >= start_i else 0.0
            if conf < cfg.min_confidence:
                continue  # spurious attack/breath blip pyin isn't sure about
            seg_rms = float(np.mean(rms[start_i : end_i + 1]))
            accepted.append((int(pitch), start_t, float(dur), conf, seg_rms))

        # Quantise only when the take actually SITS on this grid (the drum
        # path's trust discipline, verified needed on a real bass take: a hum
        # at an internal ~127 BPM force-snapped to the project's 120 grid
        # drifted across slots — notes sheared, collided, and got eaten).
        # The grid is phase-aligned to the performance; an alien grid leaves
        # the performed timing untouched.
        starts = [a[1] for a in accepted]
        step = (60.0 / tempo_bpm) * (4.0 / cfg.division)
        if len(starts) >= 4 and signal.grid_fit(starts, step) <= _GRID_TRUST_MAX:
            phase = signal.grid_phase(starts, step)
            accepted = [
                (p, max(0.0, (round(t / step - phase) + phase) * step), d, c, r)
                for p, t, d, c, r in accepted
            ]

        # Velocities normalised against the take's own dynamics, not mic gain.
        # Gentler anchors than drums: a hummed line's quiet notes are weak
        # phonation, not ghost-note intent — they must stay audible.
        velocities = signal.velocities_from_rms(
            [a[4] for a in accepted], lo=60.0, hi=115.0, floor=40.0
        )
        notes = [
            NoteEvent(
                time_s=t_q,
                midi_note=pitch,
                velocity=vel,
                duration_s=dur,
                confidence=round(conf, 3),
            )
            for (pitch, t_q, dur, conf, _r), vel in zip(accepted, velocities)
        ]
        notes.sort(key=lambda nt: (nt.time_s, nt.midi_note))
        return _enforce_monophony(notes)


def _gap_continuous(rms, a_end: int, b_start: int, dip_ratio: float = 0.5) -> bool:
    """Did the sound continue through the unvoiced gap between two segments?

    True = pyin flicker during a held tone (RMS stays up) — bridge it.
    False = the level dipped toward silence — that's a re-articulation and
    the segments are separate notes.
    """
    gap = rms[a_end + 1 : b_start]
    if gap.size == 0:
        return True
    edges = np.concatenate([rms[max(0, a_end - 2) : a_end + 1], rms[b_start : b_start + 3]])
    level = float(np.mean(edges)) if edges.size else 0.0
    return level <= 0 or float(np.min(gap)) > dip_ratio * level


def _enforce_monophony(notes: list[NoteEvent]) -> list[NoteEvent]:
    """One voice: a hummed line can never sound two notes at once.

    Verified on a real take: pitch-wobble fragments (a scoop crossing 39/40)
    survive segmentation as separate notes whose starts then quantise into
    the SAME grid slot — the clip plays adjacent-semitone clusters, which is
    the single most audible quality killer on the bass voice. Rules:

    - Notes sharing a quantised start: keep the most confident (ties -> the
      longer one); the cluster's duration is the survivors' max so nothing
      audibly shortens.
    - A note overlapping the NEXT note's start is clamped to end there
      (legato, never overlapped).
    """
    if len(notes) <= 1:
        return notes
    out: list[NoteEvent] = []
    for n in notes:
        if out and abs(n.time_s - out[-1].time_s) < 1e-6:
            prev = out[-1]
            best, other = (n, prev) if _mono_rank(n) > _mono_rank(prev) else (prev, n)
            dur = max(best.duration_s or 0.0, other.duration_s or 0.0) or None
            out[-1] = replace(best, duration_s=dur)
            continue
        out.append(n)
    for i in range(len(out) - 1):
        n, nxt = out[i], out[i + 1]
        if n.duration_s is not None and n.time_s + n.duration_s > nxt.time_s:
            out[i] = replace(n, duration_s=max(0.05, nxt.time_s - n.time_s))
    return out


def _mono_rank(n: NoteEvent) -> tuple[float, float]:
    return (n.confidence or 0.0, n.duration_s or 0.0)


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
