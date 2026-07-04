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
    assert tonal._root_midi("bass", "E") == 28   # E1
    assert 28 <= tonal._root_midi("bass", "Bb") <= 40
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
