# Bridge fork — extra Remote Script commands

Two optional commands the stock ahujasid/ableton-mcp Remote Script lacks. Splice
each into the installed script (see install steps below); both are reference
sources here, verified at runtime in Live.

## `get_selected_clip` — transcribe the clip you've selected

So `mouthflow transcribe-clip` can read the **selected** clip's audio file and
transcribe it directly (no mic, no acoustic re-recording, no file-hunting).
Source: [`get_selected_clip.py`](get_selected_clip.py). Returns
`{name, file_path, is_audio}` for Live's detail-view clip. Without it,
`transcribe-clip` exits with "is the forked bridge installed?" — and you can
still transcribe by passing a path: `mouthflow run "<clip.aif>"`.

## `set_clip_envelope` — drone contour automation

The drone device renders the performance's **loudness contour** as clip
automation on a device macro (the "contour → movement" half of the ambient
voice). The stock [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)
Remote Script has **no** parameter/automation/envelope command, so we add one.

Everything else in the umbrella works without this — drone still plays as a
**held note / chord** if the command is absent. `apply_plan` calls
`set_clip_envelope` best-effort and logs `automation skipped (...)` when the
stock bridge rejects it.

## What it adds

A `set_clip_envelope` socket command that writes a clip automation envelope for
a device parameter:

```
{"type": "set_clip_envelope",
 "params": {"track_index": N, "clip_index": 0, "device_index": 0,
            "parameter": "Macro 1", "steps": [[time_in_beats, value_0_1], ...]}}
```

Values are scaled into the parameter's real `[min, max]`; the curve is written
as consecutive `insert_step` steps (the Live clip-envelope API offers stepped
breakpoints, not curves).

## Install (manual — verify in Live)

The Remote Script lives at
`~/Music/Ableton/User Library/Remote Scripts/AbletonMCP/__init__.py`.

1. **Back it up** (`cp __init__.py __init__.py.bak`).
2. In the `_handle_client` dispatch chain (the long `elif command_type == ...`
   ladder, ~line 300), add the branch shown in
   [`set_clip_envelope.py`](set_clip_envelope.py) (step 1 of its docstring).
3. Copy the two methods `_set_clip_envelope` and `_resolve_parameter` from
   [`set_clip_envelope.py`](set_clip_envelope.py) into the `AbletonMCP` class.
4. Re-enable the Control Surface (or restart Live) so the script reloads.

> Auto-patching the user's Live script is intentionally **not** done — editing a
> live Remote Script blind is risky. Splice by hand and confirm in Live.

## Runtime verification checklist (the parts this repo can't test offline)

- **`clip.automation_envelope(param)` support** on your Live 12 version for a
  device **macro** parameter on a **MIDI** clip. If it returns `None` or
  errors, the LOM path is too thin on your build — fall back to the
  stepped-notes approach (a sequence of overlapping notes + velocity, which
  stays inside the stock bridge).
- **Macro resolution.** `_resolve_parameter` matches `"Macro 1"` on an
  Instrument Rack. If the chosen pad is a bare device (not wrapped in a rack),
  it falls back to the first non-`Device On` parameter — confirm that lands on
  something musical (e.g. a filter cutoff), or wrap pads in an Instrument Rack
  and map Macro 1 to the cutoff.
- **Beat timeline.** `steps` times are in beats from clip start; confirm they
  align with the clip the planner created (`length_bars`).
