"""Tests for the eval harness itself — the gates must be able to fail, and
the scorers must not hide failure modes (wrong-pitch matches, misaligned
labels, constant-velocity ground truth)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: eval/, mimic/

from eval.note_eval import match_stats
from eval.run_eval import ClipResult, _spearman, _swing_lean, gates_pass
from mimic.take import _labels_look_sane


def _clip(name="c", tp=10, fp=0, fn=0, correct=10, matched=10,
          tempo_hit=True, timing=20.0) -> ClipResult:
    return ClipResult(
        name=name, onset_tp=tp, onset_fp=fp, onset_fn=fn,
        class_correct=correct, class_matched=matched,
        tempo_det_bpm=100.0, tempo_gt_bpm=100.0, tempo_err_bpm=0.0,
        tempo_hit=tempo_hit, timing_mae_ms=timing,
    )


# --- gates can actually fail --------------------------------------------------

def test_gates_pass_on_healthy_results():
    assert gates_pass([_clip("a"), _clip("b")]) is True


def test_gates_fail_on_low_f1():
    # heavy false positives push F1 under 0.75
    assert gates_pass([_clip(tp=5, fp=10, fn=5, correct=5, matched=5)]) is False


def test_gates_fail_on_low_class_acc():
    assert gates_pass([_clip(correct=3, matched=10)]) is False


def test_gates_fail_on_missed_tempo():
    assert gates_pass([_clip(tempo_hit=False), _clip("b", tempo_hit=False)]) is False


def test_gates_fail_on_timing_mae():
    assert gates_pass([_clip(timing=80.0)]) is False


# --- feel metrics -------------------------------------------------------------

def test_spearman_none_for_constant_series():
    # GT velocity is a flat 90 today: correlation must be n/a, not fake-perfect.
    assert _spearman([90.0, 90.0, 90.0, 90.0], [10.0, 20.0, 30.0, 40.0]) is None


def test_spearman_detects_monotone_agreement():
    rho = _spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 25.0, 90.0])
    assert rho == pytest.approx(1.0)
    rho = _spearman([1.0, 2.0, 3.0, 4.0], [9.0, 7.0, 5.0, 1.0])
    assert rho == pytest.approx(-1.0)


def test_swing_lean_measures_late_offbeats():
    # 16ths at 120 BPM (step 125 ms), off-beats 30 ms late = swung feel.
    step = 60.0 / 120.0 / 4.0
    times = []
    for n in range(8):
        times.append(n * 2 * step)                 # on-beats on the grid
        times.append((n * 2 + 1) * step + 0.030)   # off-beats late
    lean = _swing_lean(times, 120.0)
    assert lean is not None
    on, off, n_off = lean
    assert n_off == 8
    assert off - on == pytest.approx(30.0, abs=3.0)


# --- note_eval: wrong-pitch matches are precision failures --------------------

def test_match_stats_counts_wrong_pitch_against_precision():
    ref = [(0.0, 40), (0.5, 43), (1.0, 45)]
    pred = [(0.0, 40), (0.5, 44), (1.0, 45)]  # middle note a semitone off
    s = match_stats(pred, ref, offset=0.0)
    assert s["tp"] == 2
    assert s["wrong_pitch"] == 1
    assert s["precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["fn"] == 0  # all refs were time-matched; the failure is pitch, not miss


def test_match_stats_octave_errors_reported_separately():
    ref = [(0.0, 40)]
    pred = [(0.0, 52)]  # right pitch class, +1 octave
    s = match_stats(pred, ref, offset=0.0)
    assert s["tp"] == 0 and s["octave_err"] == 1 and s["wrong_pitch"] == 0
    assert s["precision"] == 0.0


# --- mimic label sanity gate ----------------------------------------------------

def test_label_sanity_rejects_sparse_matches():
    ok, why = _labels_look_sane(matched=4, total=20, heuristic_correct=4)
    assert not ok and "doesn't follow the reference" in why


def test_label_sanity_rejects_misaligned_labels():
    ok, why = _labels_look_sane(matched=20, total=24, heuristic_correct=3)
    assert not ok and "alignment" in why


def test_label_sanity_accepts_healthy_take():
    ok, _ = _labels_look_sane(matched=20, total=24, heuristic_correct=14)
    assert ok
