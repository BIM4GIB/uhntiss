"""Run the Mouthflow pipeline over the fixture corpus and emit a report.

Walks ``tests/fixtures/clips/*.wav``, pairs each with ``*.mid`` ground
truth and ``*.json`` metadata, runs ``transcribe_drums``, and reports:

- Onset F1 (tolerance 50 ms)
- Drum-class top-1 accuracy (over matched onsets)
- Tempo within +/- 3 BPM

Fully offline — no Claude calls, no API key needed.
Taste review is interactive and lives in ``taste_review.py``.

Run: ``uv run python -m eval.run_eval`` from the repo root.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import mido

from mouthflow.transcribe import transcribe_drums

ONSET_TOLERANCE_S = 0.050
TEMPO_TOLERANCE_BPM = 3.0

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = REPO_ROOT / "tests" / "fixtures" / "clips"


@dataclass
class ClipResult:
    name: str
    onset_tp: int
    onset_fp: int
    onset_fn: int
    class_correct: int
    class_matched: int
    tempo_det_bpm: float
    tempo_gt_bpm: float | None
    tempo_err_bpm: float
    tempo_hit: bool


def _load_ground_truth(mid_path: Path) -> list[tuple[float, int]]:
    """Return (time_s, pitch) for each note_on in the GT MIDI.

    Assumes the file's tempo is set via set_tempo meta (or 120 BPM default).
    """
    mid = mido.MidiFile(mid_path)
    tempo = 500_000  # 120 BPM default
    events: list[tuple[float, int]] = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
            elif msg.type == "note_on" and msg.velocity > 0:
                seconds_per_tick = tempo / 1_000_000 / mid.ticks_per_beat
                events.append((abs_tick * seconds_per_tick, int(msg.note)))
    events.sort()
    return events


def _match_onsets(
    pred: list[tuple[float, int]], gt: list[tuple[float, int]]
) -> tuple[int, int, int, list[tuple[int, int]]]:
    """Greedy one-to-one match within tolerance. Returns TP, FP, FN, and
    a list of (pred_pitch, gt_pitch) for matched pairs."""
    used_gt: set[int] = set()
    tp = 0
    matches: list[tuple[int, int]] = []
    for p_t, p_note in pred:
        best_j = -1
        best_d = ONSET_TOLERANCE_S + 1e-9
        for j, (g_t, _g_note) in enumerate(gt):
            if j in used_gt:
                continue
            d = abs(p_t - g_t)
            if d <= ONSET_TOLERANCE_S and d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            used_gt.add(best_j)
            tp += 1
            matches.append((p_note, gt[best_j][1]))
    fp = len(pred) - tp
    fn = len(gt) - len(used_gt)
    return tp, fp, fn, matches


def _evaluate_clip(wav: Path) -> ClipResult | None:
    mid_path = wav.with_suffix(".mid")
    json_path = wav.with_suffix(".json")
    if not mid_path.exists():
        print(f"  skip: no ground-truth MIDI for {wav.name}", file=sys.stderr)
        return None

    gt_notes = _load_ground_truth(mid_path)
    gt_tempo = json.loads(json_path.read_text())["tempo"] if json_path.exists() else None

    transcription = transcribe_drums(wav)
    pred_notes = [(h.time_s, h.midi_note) for h in transcription.hits]

    tp, fp, fn, matches = _match_onsets(pred_notes, gt_notes)
    class_correct = sum(1 for p, g in matches if p == g)

    tempo_err = (
        abs(transcription.tempo_bpm - gt_tempo) if gt_tempo is not None else float("nan")
    )
    tempo_hit = gt_tempo is not None and tempo_err <= TEMPO_TOLERANCE_BPM

    return ClipResult(
        name=wav.stem,
        onset_tp=tp,
        onset_fp=fp,
        onset_fn=fn,
        class_correct=class_correct,
        class_matched=len(matches),
        tempo_det_bpm=transcription.tempo_bpm,
        tempo_gt_bpm=gt_tempo,
        tempo_err_bpm=tempo_err,
        tempo_hit=tempo_hit,
    )


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def _format_report(results: list[ClipResult]) -> str:
    n = len(results)
    tp = sum(r.onset_tp for r in results)
    fp = sum(r.onset_fp for r in results)
    fn = sum(r.onset_fn for r in results)
    f1 = _f1(tp, fp, fn)

    class_correct = sum(r.class_correct for r in results)
    class_matched = sum(r.class_matched for r in results)
    class_acc = class_correct / class_matched if class_matched else 0.0

    tempo_hits = sum(1 for r in results if r.tempo_hit)

    def tick(ok: bool) -> str:
        return "OK" if ok else "MISS"

    # Per-clip breakdown — with N this small, the aggregate hides which clip
    # regressed and whether a miss is an onset problem or a tempo problem.
    per_clip = [
        "per clip:",
        f"  {'name':<22} {'P':>4} {'R':>4} {'F1':>5}  {'det/gt bpm':>11} {'Δ':>5}",
    ]
    for r in sorted(results, key=lambda r: r.name):
        matched_p = r.onset_tp + r.onset_fp
        matched_r = r.onset_tp + r.onset_fn
        p = r.onset_tp / matched_p if matched_p else 0.0
        rec = r.onset_tp / matched_r if matched_r else 0.0
        gt = f"{r.tempo_gt_bpm:g}" if r.tempo_gt_bpm is not None else "?"
        per_clip.append(
            f"  {r.name:<22} {p:>4.2f} {rec:>4.2f} {_f1(r.onset_tp, r.onset_fp, r.onset_fn):>5.2f}"
            f"  {r.tempo_det_bpm:>5.1f}/{gt:<5} {r.tempo_err_bpm:>5.1f} {tick(r.tempo_hit)}"
        )

    today = date.today().isoformat()
    return "\n".join(
        [
            f"MOUTHFLOW EVAL - {today}",
            "-" * 30,
            *per_clip,
            "",
            f"Transcription (N={n})",
            f"  onset F1:         {f1:.2f}   {tick(f1 >= 0.75)} (target 0.75)",
            f"  drum class acc:   {class_acc:.2f}   {tick(class_acc >= 0.65)} (target 0.65)",
            f"  tempo within +-3: {tempo_hits}/{n}  {tick(tempo_hits >= max(1, math.ceil(n * 0.8)))} (target >=80%)",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()

    wavs = sorted(args.corpus.glob("*.wav"))
    if not wavs:
        print(f"No clips found in {args.corpus}. Drop your 20 beatbox WAVs there first.")
        return 1

    print(f"Running eval over {len(wavs)} clips in {args.corpus}...\n")
    results: list[ClipResult] = []
    for wav in wavs:
        print(f"  {wav.name}")
        res = _evaluate_clip(wav)
        if res is not None:
            results.append(res)

    if not results:
        print("No clips had paired ground-truth MIDI; nothing to score.")
        return 1

    print()
    print(_format_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
