"""Arrangement planner: Claude call producing a structured Plan."""

from __future__ import annotations

from mouthflow.schemas import Plan, Transcription


def make_plan(
    transcription: Transcription,
    session_state: dict,
    user_hint: str | None = None,
) -> Plan:
    raise NotImplementedError
