"""Run the Mouthflow pipeline over the fixture corpus and emit a report.

Walks ``tests/fixtures/clips/*.wav``, pairs each with ``*.mid`` ground
truth and ``*.json`` metadata, runs ``transcribe_drums``, and reports:

- Onset F1 (tolerance 50 ms)                              [gated]
- Drum-class top-1 accuracy (over matched onsets)         [gated]
- Tempo within +/- 3 BPM                                  [gated]
- Timing fidelity: mean |pred - GT| over matched onsets   [gated]
- Swing preservation + velocity rank correlation          [report-only until
  real labelled takes with feel/dynamics exist — GT velocity is currently a
  constant 90, so correlation is undefined]

The gates FAIL the run (non-zero exit) so CI can enforce them; pass
``--report-only`` to disable. Fully offline — no Claude calls, no API key.
Taste review is interactive and lives in ``taste_review.py``.

HONESTY NOTE: the default fixture WAVs originate from the k-NN classifier's
own training takes (mimic/take.py both labels the training data and emits the
fixtures), so the class-accuracy number here is a train-set UPPER BOUND, not
generalisation. The honest held-out number lives in
``uv run python -m eval.classifier_cv`` (≈0.73 at time of writing). See
docs/roadmap.md §3 (E1). This report prints a reminder when run on the
default corpus.

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
# Baseline 2026-07-03: ~38 ms, dominated by sub-BPM tempo-refine drift against
# the fixture's nominal grid (84.1 vs 84.0 over ~40 beats ≈ 36 ms), not by
# per-hit jitter. Tighten once groove-aware quantisation lands.
TIMING_MAE_TARGET_MS = 45.0

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
    timing_mae_ms: float | None = None  # mean |pred - GT| over matched onsets
    swing_err_ms: float | None = None   # |pred swing lean - GT swing lean|
    vel_spearman: float | None = None   # rank corr of velocities (None: GT constant)


def _load_ground_truth(mid_path: Path) -> list[tuple[float, int, int]]:
    """Return (time_s, pitch, velocity) for each note_on in the GT MIDI.

    Assumes the file's tempo is set via set_tempo meta (or 120 BPM default).
    """
    mid = mido.MidiFile(mid_path)
    tempo = 500_000  # 120 BPM default
    events: list[tuple[float, int, int]] = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
            elif msg.type == "note_on" and msg.velocity > 0:
                seconds_per_tick = tempo / 1_000_000 / mid.ticks_per_beat
                events.append((abs_tick * seconds_per_tick, int(msg.note), int(msg.velocity)))
    events.sort()
    return events


def _match_onsets(
    pred: list[tuple[float, int, int]], gt: list[tuple[float, int, int]]
) -> tuple[int, int, int, list[tuple[int, int]]]:
    """Greedy one-to-one match within tolerance. Returns TP, FP, FN, and
    matched (pred_index, gt_index) pairs (so callers can compare pitch,
    timing, and velocity per match)."""
    used_gt: set[int] = set()
    tp = 0
    matches: list[tuple[int, int]] = []
    for i, (p_t, _p_note, _p_vel) in enumerate(pred):
        best_j = -1
        best_d = ONSET_TOLERANCE_S + 1e-9
        for j, (g_t, _g_note, _g_vel) in enumerate(gt):
            if j in used_gt:
                continue
            d = abs(p_t - g_t)
            if d <= ONSET_TOLERANCE_S and d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            used_gt.add(best_j)
            tp += 1
            matches.append((i, best_j))
    fp = len(pred) - tp
    fn = len(gt) - len(used_gt)
    return tp, fp, fn, matches


def _spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman rank correlation; None when undefined (short or constant)."""
    import numpy as np

    if len(a) < 3 or len(a) != len(b):
        return None
    xa, xb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if np.std(xa) == 0 or np.std(xb) == 0:
        return None  # constant series (e.g. GT velocity is a flat 90)

    def ranks(x):
        order = np.argsort(x)
        r = np.empty(len(x))
        r[order] = np.arange(len(x), dtype=float)
        # average ties so equal values share a rank
        for v in np.unique(x):
            m = x == v
            r[m] = r[m].mean()
        return r

    ra, rb = ranks(xa), ranks(xb)
    denom = ra.std() * rb.std()
    if denom == 0:
        return None
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / denom)


def _swing_lean(times: list[float], bpm: float) -> tuple[float, float, int] | None:
    """(on_beat_ms, off_beat_ms, n_off): mean pre-snap residual per 16th-slot
    parity — the swing signature ``eval/timing_probe.py`` measures. None when
    there's too little signal (< 3 off-beat samples)."""
    import numpy as np

    if not times or bpm <= 0:
        return None
    t = np.asarray(times, dtype=float)
    step = 60.0 / bpm / 4.0
    frac = (t / step) % 1.0
    phase = float(np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi))
    lean = {0: [], 1: []}
    for x in t:
        idx = int(round(x / step - phase))
        resid = (x - (idx + phase) * step) * 1000.0
        lean[idx % 2].append(resid)
    if len(lean[1]) < 3:
        return None
    on = float(np.mean(lean[0])) if lean[0] else 0.0
    off = float(np.mean(lean[1]))
    return on, off, len(lean[1])


def _evaluate_clip(wav: Path) -> ClipResult | None:
    mid_path = wav.with_suffix(".mid")
    json_path = wav.with_suffix(".json")
    if not mid_path.exists():
        print(f"  skip: no ground-truth MIDI for {wav.name}", file=sys.stderr)
        return None

    gt_notes = _load_ground_truth(mid_path)
    gt_tempo = json.loads(json_path.read_text())["tempo"] if json_path.exists() else None

    transcription = transcribe_drums(wav)
    pred_notes = [(h.time_s, h.midi_note, h.velocity) for h in transcription.hits]

    tp, fp, fn, matches = _match_onsets(pred_notes, gt_notes)
    class_correct = sum(1 for i, j in matches if pred_notes[i][1] == gt_notes[j][1])

    tempo_err = (
        abs(transcription.tempo_bpm - gt_tempo) if gt_tempo is not None else float("nan")
    )
    tempo_hit = gt_tempo is not None and tempo_err <= TEMPO_TOLERANCE_BPM

    # Feel metrics over matched pairs: timing fidelity, swing preservation,
    # and velocity rank correlation (None while GT velocity is constant).
    timing_mae = None
    swing_err = None
    vel_rho = None
    if matches:
        timing_mae = 1000.0 * sum(
            abs(pred_notes[i][0] - gt_notes[j][0]) for i, j in matches
        ) / len(matches)
        vel_rho = _spearman(
            [float(pred_notes[i][2]) for i, j in matches],
            [float(gt_notes[j][2]) for i, j in matches],
        )
        if gt_tempo:
            gt_lean = _swing_lean([gt_notes[j][0] for _, j in matches], gt_tempo)
            pred_lean = _swing_lean([pred_notes[i][0] for i, _ in matches], gt_tempo)
            if gt_lean and pred_lean:
                swing_err = abs((pred_lean[1] - pred_lean[0]) - (gt_lean[1] - gt_lean[0]))

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
        timing_mae_ms=timing_mae,
        swing_err_ms=swing_err,
        vel_spearman=vel_rho,
    )


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def _aggregate(results: list[ClipResult]) -> dict:
    n = len(results)
    tp = sum(r.onset_tp for r in results)
    fp = sum(r.onset_fp for r in results)
    fn = sum(r.onset_fn for r in results)
    class_correct = sum(r.class_correct for r in results)
    class_matched = sum(r.class_matched for r in results)
    timing = [r.timing_mae_ms for r in results if r.timing_mae_ms is not None]
    swing = [r.swing_err_ms for r in results if r.swing_err_ms is not None]
    vels = [r.vel_spearman for r in results if r.vel_spearman is not None]
    return {
        "n": n,
        "f1": _f1(tp, fp, fn),
        "class_acc": class_correct / class_matched if class_matched else 0.0,
        "tempo_hits": sum(1 for r in results if r.tempo_hit),
        "timing_mae_ms": sum(timing) / len(timing) if timing else None,
        "swing_err_ms": sum(swing) / len(swing) if swing else None,
        "vel_spearman": sum(vels) / len(vels) if vels else None,
    }


def gates_pass(results: list[ClipResult]) -> bool:
    """The CI contract: every gated metric at or above target."""
    a = _aggregate(results)
    return (
        a["f1"] >= 0.75
        and a["class_acc"] >= 0.65
        and a["tempo_hits"] >= max(1, math.ceil(a["n"] * 0.8))
        and (a["timing_mae_ms"] is None or a["timing_mae_ms"] <= TIMING_MAE_TARGET_MS)
    )


def _format_report(results: list[ClipResult]) -> str:
    a = _aggregate(results)
    n = a["n"]

    def tick(ok: bool) -> str:
        return "OK" if ok else "MISS"

    # Per-clip breakdown — with N this small, the aggregate hides which clip
    # regressed and whether a miss is an onset problem or a tempo problem.
    per_clip = [
        "per clip:",
        f"  {'name':<22} {'P':>4} {'R':>4} {'F1':>5}  {'det/gt bpm':>11} {'Δ':>5}  {'t-mae':>6}",
    ]
    for r in sorted(results, key=lambda r: r.name):
        matched_p = r.onset_tp + r.onset_fp
        matched_r = r.onset_tp + r.onset_fn
        p = r.onset_tp / matched_p if matched_p else 0.0
        rec = r.onset_tp / matched_r if matched_r else 0.0
        gt = f"{r.tempo_gt_bpm:g}" if r.tempo_gt_bpm is not None else "?"
        tmae = f"{r.timing_mae_ms:>4.0f}ms" if r.timing_mae_ms is not None else "   n/a"
        per_clip.append(
            f"  {r.name:<22} {p:>4.2f} {rec:>4.2f} {_f1(r.onset_tp, r.onset_fp, r.onset_fn):>5.2f}"
            f"  {r.tempo_det_bpm:>5.1f}/{gt:<5} {r.tempo_err_bpm:>5.1f} {tick(r.tempo_hit)}  {tmae}"
        )

    swing = f"{a['swing_err_ms']:.0f} ms" if a["swing_err_ms"] is not None else "n/a (too few off-beat samples)"
    vel = f"{a['vel_spearman']:+.2f}" if a["vel_spearman"] is not None else "n/a (GT velocity is constant — see roadmap §3 E5)"
    tmae_line = (
        f"  timing MAE:       {a['timing_mae_ms']:.0f} ms  {tick(a['timing_mae_ms'] <= TIMING_MAE_TARGET_MS)} (target <={TIMING_MAE_TARGET_MS:.0f} ms)"
        if a["timing_mae_ms"] is not None
        else "  timing MAE:       n/a"
    )

    today = date.today().isoformat()
    return "\n".join(
        [
            f"MOUTHFLOW EVAL - {today}",
            "-" * 30,
            *per_clip,
            "",
            f"Transcription (N={n})",
            f"  onset F1:         {a['f1']:.2f}   {tick(a['f1'] >= 0.75)} (target 0.75)",
            f"  drum class acc:   {a['class_acc']:.2f}   {tick(a['class_acc'] >= 0.65)} (target 0.65)",
            f"  tempo within +-3: {a['tempo_hits']}/{n}  {tick(a['tempo_hits'] >= max(1, math.ceil(n * 0.8)))} (target >=80%)",
            tmae_line,
            f"  swing error:      {swing}  (report-only)",
            f"  velocity rank-r:  {vel}  (report-only)",
            "",
        ]
    )


_CONTAMINATION_WARNING = """\
!! HONESTY: the default fixtures originate from the k-NN classifier's own
!! training takes (train-on-test) — class acc here is an UPPER BOUND, not
!! generalisation. Held-out number: `uv run python -m eval.classifier_cv`
!! (≈0.73 at time of writing). N is tiny; see docs/roadmap.md §3 (E1/E2).
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--report-only", action="store_true",
        help="Print the report but always exit 0 (gates off).",
    )
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
    if args.corpus.resolve() == DEFAULT_CORPUS.resolve():
        print(_CONTAMINATION_WARNING)

    if not gates_pass(results):
        print("EVAL GATES FAILED — a gated metric is below target.")
        return 0 if args.report_only else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
