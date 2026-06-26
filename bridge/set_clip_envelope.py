"""Forked ableton-mcp bridge command: ``set_clip_envelope``.

The stock ahujasid/ableton-mcp Remote Script exposes no automation/parameter
write. The drone device's "contour -> automation" feature needs one, so this
module is the **reference source** for the command to splice into the installed
Remote Script (``~/Music/Ableton/User Library/Remote Scripts/AbletonMCP/__init__.py``).
See ``bridge/README.md`` for install steps.

It is written against the Ableton Live Object Model and runs *inside* Live (it
can't be unit-tested from this repo). The client side
(``mouthflow.execute.AbletonClient.set_clip_envelope``) and the
serialization/degrade-gracefully behaviour in ``apply_plan`` ARE tested.

Integration (two edits to the Remote Script's ``AbletonMCP`` class):

1. Add a dispatch branch in ``_handle_client`` alongside the other
   ``elif command_type == ...`` blocks::

        elif command_type == "set_clip_envelope":
            result = self._set_clip_envelope(
                params.get("track_index", 0),
                params.get("clip_index", 0),
                params.get("device_index", 0),
                params.get("parameter", "Macro 1"),
                params.get("steps", []),
            )

2. Add the two methods below to the class (copy the bodies verbatim).
"""

from __future__ import annotations


class ClipEnvelopeMixin:
    """Reference impl — copy these methods into the Remote Script's class.

    (Presented as a mixin only so it lints cleanly; the Remote Script is a
    single class, so paste the method bodies into it.)
    """

    def _set_clip_envelope(self, track_index, clip_index, device_index, parameter, steps):
        """Write a clip automation envelope for a device parameter.

        ``steps`` is a list of ``[time_in_beats, value_0_1]``. Values are scaled
        into the parameter's real ``[min, max]`` and written as consecutive
        constant steps (a stepped approximation of the contour — the Live clip
        envelope API exposes ``insert_step``, not curved breakpoints).
        """
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            clip_slot = track.clip_slots[clip_index]
            if not clip_slot.has_clip:
                raise Exception("No clip in slot")
            clip = clip_slot.clip

            if device_index < 0 or device_index >= len(track.devices):
                raise IndexError("Device index out of range")
            device = track.devices[device_index]

            param = self._resolve_parameter(device, parameter)
            if param is None:
                raise Exception("Parameter not found: " + str(parameter))

            # automation_envelope() returns (creating if needed) the clip's
            # envelope for an automatable parameter.
            envelope = clip.automation_envelope(param)
            if envelope is None:
                raise Exception("No automation envelope for parameter: " + str(param.name))
            envelope.clear()

            p_min, p_max = param.min, param.max
            n = len(steps)
            for i in range(n):
                t = float(steps[i][0])
                v = float(steps[i][1])
                v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
                scaled = p_min + v * (p_max - p_min)
                if i + 1 < n:
                    length = max(0.0, float(steps[i + 1][0]) - t)
                else:
                    length = max(0.0, float(clip.length) - t)
                envelope.insert_step(t, length, scaled)

            return {"parameter": param.name, "steps": n}
        except Exception as e:  # noqa: BLE001 — mirror the Remote Script style
            self.log_message("Error setting clip envelope: " + str(e))
            raise

    def _resolve_parameter(self, device, name):
        """Find a device parameter by name, with macro + sensible fallbacks."""
        params = list(device.parameters)
        for p in params:
            if p.name == name:
                return p
        if str(name).lower().startswith("macro"):
            for p in params:
                if p.name.lower().startswith("macro"):
                    return p
        for p in params:
            if p.name not in ("Device On",):
                return p
        return params[0] if params else None
