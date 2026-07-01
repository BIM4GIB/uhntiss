"""Forked ableton-mcp bridge command: ``get_selected_clip``.

Lets ``mouthflow transcribe-clip`` read the clip you've SELECTED in Live (the
one shown in the detail view) and transcribe its audio file directly — no mic,
no acoustic re-recording, no manual file-hunting. The stock Remote Script has
no way to report the selected clip's sample path, so we add one.

Runs *inside* Live against the Live Object Model (can't be unit-tested from this
repo). The client side (``execute.AbletonClient.get_selected_clip``) IS tested.

Integration (two edits to the Remote Script's ``AbletonMCP`` class):

1. Dispatch branch in ``_handle_client``::

        elif command_type == "get_selected_clip":
            result = self._get_selected_clip()

2. Add the method below to the class.
"""

from __future__ import annotations


class SelectedClipMixin:
    """Reference impl — copy ``_get_selected_clip`` into the Remote Script class."""

    def _get_selected_clip(self):
        """Return ``{name, file_path, is_audio}`` for Live's detail-view clip.

        ``file_path`` is the on-disk sample for an audio clip (empty for MIDI or
        when nothing is selected). ``mouthflow transcribe-clip`` feeds it to the
        pipeline.
        """
        try:
            clip = self._song.view.detail_clip
            if clip is None:
                return {"name": None, "file_path": None, "is_audio": False}
            is_audio = bool(getattr(clip, "is_audio_clip", False))
            file_path = getattr(clip, "file_path", "") if is_audio else ""
            return {"name": clip.name, "file_path": file_path, "is_audio": is_audio}
        except Exception as e:  # noqa: BLE001 — mirror the Remote Script style
            self.log_message("Error getting selected clip: " + str(e))
            raise
