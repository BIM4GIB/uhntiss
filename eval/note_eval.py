"""Note-level scoring for the pitched voices (bass / lead).

The drum-quality work showed: you can't improve what you can't measure. This is
the pitched analogue of ``classifier_cv`` — given a take that imitates a known
reference melody (onset + MIDI pitch per note), it aligns the transcription to
the reference and scores:

- note precision / recall / F1 (a note is correct if onset is within tolerance
  AND pitch matches), and
- octave-error rate (right pitch class, wrong octave — the classic humming
  failure), separated out so we can tell octave mistakes from real misses.

Reference grids live in ``mimic/<name>.notegrid.json`` as
``{"bpm": N, "notes": [[time_s, midi], ...]}``; the take is
``mimic/<name>.hum.wav``. A built-in synthetic self-test confirms the scorer +
transcriber agree on clean tones.

Run: ``uv run python -m eval.note_eval``
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mouthflow.devices.bass.device import BASS_CONFIG
from mouthflow.devices.lead.device import LEAD_CONFIG
from mouthflow.devices.pitched import PitchedTranscriber

MIMIC = Path(__file__).resolve().parent.parent / "mimic"
ONSET_TOL = 0.12  # seconds; a predicted note must land this close to the reference

# The offset compensates reaction time + audio latency, which are small. A
# ±0.5s free search could slide a whole note slot (at 100 BPM 8ths, slots are
# 0.3s apart) and lock onto an off-by-one alignment with equal match count —
# the slot-aliasing failure the drum harness (mimic/take.py) guards against.
OFFSET_MAX = 0.15


def _align_offset(pred_t: np.ndarray, ref_t: np.ndarray) -> float:
    """Best constant offset (pred = ref + offset) by max onset matches.

    Ties are broken toward the smallest |offset| — with a dense grid, several
    offsets can match equally by count, and the least-shifted alignment is the
    physically plausible one."""
    if pred_t.size == 0 or ref_t.size == 0:
        return 0.0
    best, best_key = 0.0, (-1, 0.0)
    for d in np.arange(-OFFSET_MAX, OFFSET_MAX + 1e-9, 0.005):
        n = sum(np.any(np.abs(pred_t - (tg + d)) <= ONSET_TOL) for tg in ref_t)
        key = (n, -abs(d))
        if key > best_key:
            best_key, best = key, d
    return float(best)


def match_stats(pred: list[tuple[float, int]], ref: list[tuple[float, int]], offset: float) -> dict:
    """Match predicted notes to the reference grid and count outcomes.

    Every predicted note is accounted for: correct (tp), octave error (right
    pitch class, wrong octave), wrong pitch (time-matched but a different
    pitch class — these ARE precision failures), or unmatched (fp). So
    precision == tp / len(pred), with the failure modes broken out.
    """
    used: set[int] = set()
    tp = octave = wrong = 0
    for tg, mg in ref:
        cands = [
            i for i in range(len(pred)) if i not in used and abs(pred[i][0] - (tg + offset)) <= ONSET_TOL
        ]
        if not cands:
            continue
        i = min(cands, key=lambda i: abs(pred[i][0] - (tg + offset)))
        used.add(i)
        if pred[i][1] == mg:
            tp += 1
        elif (pred[i][1] - mg) % 12 == 0:
            octave += 1
        else:
            wrong += 1
    fp = len(pred) - len(used)
    fn = len(ref) - len(used)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(ref) if ref else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "notes_ref": len(ref),
        "notes_pred": len(pred),
        "tp": tp,
        "octave_err": octave,
        "wrong_pitch": wrong,
        "fp": fp,
        "fn": fn,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "offset": round(offset, 3),
    }


def score(transcriber: PitchedTranscriber, hum_wav: Path, notegrid: dict) -> dict:
    """Note P/R/F1 + octave-error of ``transcriber`` on ``hum_wav`` vs the grid."""
    t = transcriber.transcribe(hum_wav)
    pred = [(h.time_s, h.midi_note) for h in t.hits]
    pred_t = np.array([p[0] for p in pred], dtype=float)
    ref = notegrid["notes"]
    ref_t = np.array([r[0] for r in ref], dtype=float)

    offset = _align_offset(pred_t, ref_t)
    return match_stats(pred, [(float(tg), int(mg)) for tg, mg in ref], offset)


# --- synthetic self-test (no recording needed) ---------------------------

def _synth_melody(notes_beats: list[tuple[float, float, int]], bpm: float, sr: int = 44100) -> np.ndarray:
    """notes_beats = [(start_beat, dur_beats, midi)] -> a sine-tone melody."""
    import librosa

    total = max(b + d for b, d, _ in notes_beats) * 60.0 / bpm + 0.2
    y = np.zeros(int(total * sr), dtype=np.float32)
    for sb, db, m in notes_beats:
        f = float(librosa.midi_to_hz(m))
        n = int(db * 60.0 / bpm * sr)
        tt = np.arange(n) / sr
        env = np.minimum(1.0, np.minimum(tt * 50, (db * 60.0 / bpm - tt) * 50))
        y[int(sb * 60.0 / bpm * sr) : int(sb * 60.0 / bpm * sr) + n] += (0.5 * env * np.sin(2 * np.pi * f * tt)).astype(np.float32)
    return y


def main() -> int:
    import tempfile

    import soundfile as sf

    # a simple bassline: root-fifth-octave walk, MIDI in the bass range
    bpm = 100.0
    bassline = [(0, 1, 40), (1, 1, 47), (2, 1, 52), (3, 1, 47), (4, 1, 40), (5, 1, 43), (6, 1, 45), (7, 1, 47)]
    y = _synth_melody(bassline, bpm)
    wav = Path(tempfile.mktemp(suffix=".wav"))
    sf.write(wav, y, 44100, subtype="PCM_16")
    grid = {"bpm": bpm, "notes": [[round(b * 60.0 / bpm, 4), m] for b, _, m in bassline]}

    print("synthetic self-test (clean sines should score ~1.0):")
    print(f"  bass: {score(PitchedTranscriber(BASS_CONFIG), wav, grid)}")
    print(f"  lead: {score(PitchedTranscriber(LEAD_CONFIG), wav, grid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
