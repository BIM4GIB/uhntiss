"""Explicit device registry — no import-time magic beyond each device module
registering itself when imported (see ``devices/__init__.py``)."""

from __future__ import annotations

from mouthflow.devices.base import DeviceSpec
from mouthflow.schemas import Intent

_DEVICES: dict[Intent, DeviceSpec] = {}


def register(spec: DeviceSpec) -> None:
    _DEVICES[spec.intent] = spec


def get_device(intent: Intent) -> DeviceSpec:
    if intent not in _DEVICES:
        raise KeyError(f"no device registered for intent {intent!r}")
    return _DEVICES[intent]


def get_device_by_id(device_id: str) -> DeviceSpec:
    for spec in _DEVICES.values():
        if spec.id == device_id:
            return spec
    raise KeyError(f"no device registered with id {device_id!r}")


def all_devices() -> list[DeviceSpec]:
    return list(_DEVICES.values())
