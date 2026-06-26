"""Device registry package.

Importing this package registers every built-in device (each device module
calls ``registry.register`` at import). Add new voices here as they land.
"""

from __future__ import annotations

from mouthflow.devices import registry  # noqa: F401  (re-exported convenience)
from mouthflow.devices.base import ClipMode, DeviceSpec, Transcriber  # noqa: F401
from mouthflow.devices.registry import (  # noqa: F401
    all_devices,
    get_device,
    get_device_by_id,
    register,
)

# Register built-in devices (import for side effect).
from mouthflow.devices.drum import device as _drum_device  # noqa: E402,F401
from mouthflow.devices.bass import device as _bass_device  # noqa: E402,F401
from mouthflow.devices.lead import device as _lead_device  # noqa: E402,F401
