"""Tests for the tonal dataset harness (mimic/tonal.py)."""

from __future__ import annotations

import json
import shutil

import pytest

from mimic import tonal


@pytest.fixture(autouse=True)
def _isolate_dataset_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(tonal, "HERE", tmp_path / "scratch")
    monkeypatch.setattr(tonal, "DATASETS", tmp_path / "datasets")
    (tmp_path / "scratch").mkdir()


def test_root_midi_lands_in_voice_register():
    # bass references sit where a voice can hum them (E2..D#3, 82-156 Hz); the
    # pipeline's octave snap moves the line into the bass register afterwards
    assert tonal._root_midi("bass", "E") == 40   # E2
    assert tonal._root_midi("bass", "A") == 45   # A2 = 110 Hz, what the first rows were actually hummed at
    assert 40 <= tonal._root_midi("bass", "Bb") <= 52
    assert 55 <= tonal._root_midi("lead", "C") <= 67
    assert 48 <= tonal._root_midi("drone", "F#") <= 60


def test_gen_writes_reference_and_grid(tmp_path):
    tonal.gen("t1", "bass", "pump8", "F", 100, loops=2)
    grid = json.load(open(tonal._paths("t1")["grid"]))
    riff_len = len(tonal.BASS_RIFFS["pump8"])
    assert len(grid["notes"]) == riff_len * 2  # two loops
    times = [t for t, _ in grid["notes"]]
    assert times == sorted(times)
    assert times[0] >= 4 * 60.0 / 100  # after the count-in bar
    assert tonal._paths("t1")["reference"].exists()


def test_perfect_take_passes_gate_and_ingests(tmp_path):
    # The reference itself is a perfect imitation: the transcriber must track
    # it and the trio must land in datasets/ with a manifest line.
    tonal.gen("t2", "bass", "roots", "A", 110, loops=1)
    p = tonal._paths("t2")
    shutil.copy2(p["reference"], p["take"])
    assert tonal.score("t2", role="calibrate") is True
    manifest = [json.loads(l) for l in open(tonal.DATASETS / "manifest.jsonl")]
    assert manifest[0]["voice"] == "bass" and manifest[0]["role"] == "calibrate"
    assert manifest[0]["scores"]["f1"] > 0.9
    assert (tonal.DATASETS / "bass" / "t2.hum.wav").exists()


def test_all_plan_riffs_exist():
    from mimic.session import PLANS

    for rows in PLANS.values():
        for name, voice, riff, key, bpm, role in rows:
            assert riff in tonal.RIFFS[voice], (name, voice, riff)
            tonal._root_midi(voice, key)  # raises on a bad key
            assert role in ("calibrate", "eval", "train")


# --- live-native flow (mimic/live_ingest.py) -----------------------------------


def test_live_row_notes_match_riff():
    from mimic.live_ingest import _row_notes

    notes = _row_notes("bass", "pump8", "F")
    assert len(notes) == len(tonal.BASS_RIFFS["pump8"])
    root = tonal._root_midi("bass", "F")
    assert all(n["pitch"] >= root for n in notes)
    assert all(0 <= n["start_time"] < 8.0 for n in notes)  # riffs span 2 bars


def test_live_plan_chunking_fits_default_scenes():
    from mimic.live_ingest import _SLOTS_PER_TRACK
    from mimic.session import PLANS

    for rows in PLANS.values():
        chunks = [rows[i:i + _SLOTS_PER_TRACK] for i in range(0, len(rows), _SLOTS_PER_TRACK)]
        assert all(len(c) <= 8 for c in chunks)


def test_pitch_class_metrics_forgive_the_performers_octave():
    from eval.note_eval import match_stats

    ref = [(0.0, 33), (1.0, 40), (2.0, 33)]
    pred = [(0.0, 45), (1.0, 40), (2.0, 45)]  # A1 refs hummed at A2
    st = match_stats(pred, ref, 0.0)
    assert st["tp"] == 1 and st["octave_err"] == 2
    assert st["f1"] < 0.4 and st["f1_pc"] == 1.0
