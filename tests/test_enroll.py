"""Enrollment: one-class clips -> hits -> per-user model that the classifier prefers."""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf
from typer.testing import CliRunner

from mouthflow import enroll, signal
from mouthflow.cli import app
from mouthflow.devices.drum import classify as drum_classify

SR = signal._SR


def _burst(kind: str, rng, n=2600):
    t = np.arange(n) / SR
    env = np.exp(-t / {"kick": 0.08, "snare": 0.05, "hat": 0.02, "hat_open": 0.12}[kind])
    if kind == "kick":
        x = np.sin(2 * np.pi * 60 * t) * 0.9  # plain low sine: one clean attack per hit
    elif kind == "snare":
        x = np.sin(2 * np.pi * 190 * t) * 0.4 + rng.normal(0, 0.35, n)
        x = np.convolve(x, np.ones(6) / 6, mode="same")  # band-limit the noise
    elif kind == "hat":
        x = rng.normal(0, 0.5, n)
        x = x - np.convolve(x, np.ones(12) / 12, mode="same")  # high-pass
    else:  # hat_open: brighter, longer
        x = rng.normal(0, 0.45, n)
        x = x - np.convolve(x, np.ones(8) / 8, mode="same")
    x = x * env
    x[-400:] *= np.linspace(1.0, 0.0, 400)  # no end-click: the detector would count it as a hit
    return x.astype(np.float32)


def _clip(kind: str, hits: int = 10, spacing: float = 0.45, seed: int = 0, sr: int = SR, stereo=False):
    rng = np.random.default_rng(seed)
    y = np.zeros(int((hits * spacing + 0.5) * SR), dtype=np.float32)
    for i in range(hits):
        s = int((0.2 + i * spacing) * SR)
        b = _burst(kind, rng)
        y[s : s + len(b)] += b
    y = y / (np.abs(y).max() + 1e-9) * 0.8
    if sr != SR:
        import librosa

        y = librosa.resample(y, orig_sr=SR, target_sr=sr)
    if stereo:
        y = np.stack([y, y], axis=1)
    return y


def _write(tmp_path, kind, **kw):
    sr = kw.pop("sr", SR)
    y = _clip(kind, sr=sr, **kw)
    p = tmp_path / f"{kind}-{sr}.wav"
    sf.write(p, y, sr, subtype="PCM_16")
    return p


def test_class_aliases_and_rejection():
    assert enroll.normalise_class("Open") == "hat_open"
    assert enroll.normalise_class("HH") == "hat"
    assert enroll.normalise_class("kd") == "kick"
    with pytest.raises(ValueError):
        enroll.normalise_class("cowbell")


def test_add_clip_detects_hits_copies_audio_and_updates_manifest(tmp_path):
    p = _write(tmp_path, "kick", hits=10)
    entry = enroll.add_clip("kick", p, source="file")
    assert 9 <= entry["hits"] <= 11
    assert entry["class"] == "kick" and entry["kind"] == "file"
    assert (enroll.ENROLL_DIR / entry["path"].split("/")[-1]).exists()
    st = enroll.status()
    assert st["classes"]["kick"]["clips"] == 1 and st["classes"]["kick"]["hits"] == entry["hits"]
    assert st["missing"] == ["snare", "hat"] and not st["ready"]


def test_add_clip_accepts_48k_stereo_live_recordings(tmp_path):
    p = _write(tmp_path, "snare", hits=8, sr=48000, stereo=True)
    entry = enroll.add_clip("snare", p, source="live-clip")
    assert 7 <= entry["hits"] <= 9
    info = sf.info(entry["path"])
    assert info.samplerate == SR and info.channels == 1


def test_silent_clip_is_rejected(tmp_path):
    p = tmp_path / "silent.wav"
    sf.write(p, np.zeros(SR * 2, dtype=np.float32), SR, subtype="PCM_16")
    with pytest.raises(enroll.EnrollError):
        enroll.add_clip("hat", p)


def test_train_requires_at_least_two_sounds(tmp_path):
    enroll.add_clip("kick", _write(tmp_path, "kick"))
    with pytest.raises(enroll.EnrollError, match="at least 2 sounds.*have: kick"):
        enroll.train()


def test_user_sound_registers_with_note_and_shows_in_status(tmp_path):
    with pytest.raises(ValueError, match="unknown sound 'rimshot'"):
        enroll.add_clip("rimshot", _write(tmp_path, "snare"))  # no note -> refuse (typo guard)
    e = enroll.add_clip("rimshot", _write(tmp_path, "snare"), note=37)
    assert e["class"] == "rimshot" and e["note"] == 37
    assert enroll.classes()["rimshot"] == 37
    st = enroll.status()
    assert st["classes"]["rimshot"]["hits"] > 0 and not st["classes"]["rimshot"]["builtin"]
    assert any("rimshot" in line and "note  37" in line and "your sound" in line for line in enroll.format_status(st))


def test_auto_note_picks_next_free_gm_percussion_note(tmp_path):
    e1 = enroll.add_clip("tongue_click", _write(tmp_path, "hat"), note="auto")
    e2 = enroll.add_clip("lip_buzz", _write(tmp_path, "kick"), note="auto")
    assert e1["note"] == enroll.AUTO_NOTES[0] and e2["note"] == enroll.AUTO_NOTES[1]
    with pytest.raises(ValueError, match="letters/digits/underscore"):
        enroll.set_class_note("bad name!", 60)
    with pytest.raises(ValueError, match="0-127"):
        enroll.set_class_note("too_high", 200)


def test_map_remaps_and_model_emits_user_note(tmp_path, monkeypatch):
    enroll.add_clip("kick", _write(tmp_path, "kick", hits=12))
    enroll.add_clip("tss", _write(tmp_path, "hat", hits=12), note=37)  # a hat-like custom sound
    assert enroll.set_class_note("tss", 56) == 56  # remap to cowbell
    rep = enroll.train()
    assert set(rep["hits_per_class"]) == {"kick", "tss"}
    m = json.loads(drum_classify._USER_MODEL_PATH.read_text())
    assert m["classes"] == {"kick": 36, "tss": 56}
    monkeypatch.setattr(drum_classify, "_MODEL", drum_classify._UNSET)
    y = _clip("hat", hits=3, seed=9)
    preds = [drum_classify._classify(y, SR, t, signal.features_at(y, SR, t)) for t in enroll._hits(y, SR)]
    assert preds.count(56) >= 2, preds
    # summaries name the sound the way the performer did
    assert drum_classify.note_names()[56] == "tss"


def test_train_writes_user_model_that_the_classifier_prefers(tmp_path, monkeypatch):
    for kind in ("kick", "snare", "hat"):
        enroll.add_clip(kind, _write(tmp_path, kind, hits=12))
    rep = enroll.train()
    assert rep["n"] >= 30 and rep["cv"]["accuracy"] >= 0.9, rep["cv"]
    m = json.loads(drum_classify._USER_MODEL_PATH.read_text())
    assert m["feature_set"] == "all123" and len(m["mean"]) == 123
    assert m["classes"] == {"hat": 42, "kick": 36, "snare": 38}
    assert m["source"]["kind"] == "enrollment" and m["source"]["hits_per_class"]["kick"] == rep["hits_per_class"]["kick"]

    # the classifier reloads and uses the enrolled model over the packaged one
    monkeypatch.setattr(drum_classify, "_MODEL", drum_classify._UNSET)
    assert drum_classify._get_model()["source"]["kind"] == "enrollment"
    for kind, pitch in (("kick", 36), ("snare", 38), ("hat", 42)):
        y = _clip(kind, hits=3, seed=7)
        hits = enroll._hits(y, SR)
        preds = [drum_classify._classify(y, SR, t, signal.features_at(y, SR, t)) for t in hits]
        assert preds.count(pitch) >= 2, (kind, preds)


def test_optional_open_hat_becomes_fourth_class(tmp_path):
    for kind in ("kick", "snare", "hat", "hat_open"):
        enroll.add_clip(kind, _write(tmp_path, kind, hits=enroll.MIN_HITS + 2))
    rep = enroll.train()
    m = json.loads(drum_classify._USER_MODEL_PATH.read_text())
    assert m["classes"]["hat_open"] == 46
    assert set(rep["hits_per_class"]) == {"kick", "snare", "hat", "hat_open"}


def test_thin_open_hat_is_folded_into_hat(tmp_path):
    for kind in ("kick", "snare", "hat"):
        enroll.add_clip(kind, _write(tmp_path, kind, hits=12))
    enroll.add_clip("hat_open", _write(tmp_path, "hat_open", hits=4))
    rep = enroll.train()
    assert rep["folded"] == ["hat_open"]
    assert "hat_open" not in rep["hits_per_class"]
    m = json.loads(drum_classify._USER_MODEL_PATH.read_text())
    assert "hat_open" not in m["classes"] and m["source"]["folded"] == ["hat_open"]
    assert rep["hits_per_class"]["hat"] >= 12 + 3  # the thin open hats count as hats


def test_reset_removes_clips_and_model(tmp_path):
    for kind in ("kick", "snare", "hat"):
        enroll.add_clip(kind, _write(tmp_path, kind))
    enroll.train()
    assert drum_classify._USER_MODEL_PATH.exists()
    enroll.reset()
    assert not drum_classify._USER_MODEL_PATH.exists() and not enroll.ENROLL_DIR.exists()
    assert enroll.status()["missing"] == list(enroll.CORE) and not enroll.status()["ready"]


def test_cli_file_status_train_reset(tmp_path):
    r = CliRunner(mix_stderr=True)
    for kind in ("kick", "snare", "hat"):
        res = r.invoke(app, ["enroll", "file", kind, str(_write(tmp_path, kind))])
        assert res.exit_code == 0, res.output
        assert f"enrolled {kind}" in res.output
    res = r.invoke(app, ["enroll", "status"])
    assert res.exit_code == 0 and "run `enroll train`" in res.output
    res = r.invoke(app, ["enroll", "train"])
    assert res.exit_code == 0, res.output
    assert "user model written" in res.output and "cross-validated" in res.output
    res = r.invoke(app, ["enroll", "reset"])
    assert res.exit_code == 2  # needs --yes
    assert drum_classify._USER_MODEL_PATH.exists()
    res = r.invoke(app, ["enroll", "reset", "--yes"])
    assert res.exit_code == 0 and not drum_classify._USER_MODEL_PATH.exists()


def test_cli_rejects_unknown_class(tmp_path):
    r = CliRunner(mix_stderr=True)
    res = r.invoke(app, ["enroll", "file", "cowbell", str(_write(tmp_path, "kick"))])
    assert res.exit_code == 2 and "unknown sound" in res.output and "--note" in res.output


def test_cli_train_before_clips_fails_cleanly():
    r = CliRunner(mix_stderr=True)
    res = r.invoke(app, ["enroll", "train"])
    assert res.exit_code == 2 and "at least 2 sounds" in res.output


def test_cli_custom_sound_and_map(tmp_path):
    r = CliRunner(mix_stderr=True)
    res = r.invoke(app, ["enroll", "file", "rimshot", str(_write(tmp_path, "snare"))])
    assert res.exit_code == 2 and "unknown sound" in res.output
    res = r.invoke(app, ["enroll", "file", "rimshot", str(_write(tmp_path, "snare")), "--note", "auto"])
    assert res.exit_code == 0 and f"enrolled rimshot (note {enroll.AUTO_NOTES[0]})" in res.output
    res = r.invoke(app, ["enroll", "map", "rimshot", "37"])
    assert res.exit_code == 0 and "rimshot -> note 37" in res.output
    assert enroll.classes()["rimshot"] == 37
