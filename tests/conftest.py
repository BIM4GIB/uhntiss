"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path, monkeypatch):
    """No test may touch the real ``~/.mouthflow`` (kit caches, last-take
    bookkeeping, enrollment clips, the enrolled drum model) or the take vault.

    Found the hard way: a CLI-level test exercised ``list-kits``'s cache
    write and left fake ``Kit 0..9`` URIs in the REAL user cache — a later
    real take would have planned against them and failed after the LLM call.
    """
    from mouthflow import capture, cli, enroll, pocket
    from mouthflow.devices.drum import classify as drum_classify

    monkeypatch.setattr(cli, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cli, "_LAST_TAKE", tmp_path / "state" / "last_take.json")
    monkeypatch.setattr(capture, "TAKES_DIR", tmp_path / "takes")
    monkeypatch.setattr(enroll, "ENROLL_DIR", tmp_path / "state" / "enroll")
    monkeypatch.setattr(drum_classify, "_USER_MODEL_PATH", tmp_path / "state" / "drum_model.json")
    monkeypatch.setattr(pocket, "profile_path", lambda: tmp_path / "state" / "pocket_profile.json")
    monkeypatch.setattr(pocket, "max_library_dirs", lambda: [])
