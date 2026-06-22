# Mouthflow — Max for Live device (MVP)

An in-Ableton panel for mouthflow. It does **not** re-implement the pipeline:
the device is a UI that drives the existing `mouthflow` CLI via Node for Max,
which records, transcribes, plans (Claude), and applies the clip to Live over
the ableton-mcp socket — exactly the verified command-line path.

```
[Mouthflow.amxd]  UI (buttons / fields)
      │  inlet messages
      ▼
[node.script mouthflow.js]  ──spawn──▶  uv run mouthflow record --json
      ▲                                        │ transcribe + plan + apply
      └── outlet: status / tempo / rationale ──┘  (via ableton-mcp :9877)
```

## Prerequisites
- Ableton Live 11/12 **Suite** (includes Max for Live).
- The mouthflow repo cloned and working from the CLI (`mouthflow doctor`
  passes). The device shells out to it.
- `ableton-mcp` Remote Script enabled (the apply step still uses `:9877`).

## Files (keep them together)
- `Mouthflow.amxd` — **the prebuilt device** (drop it on a track and use it)
- `mouthflow.js` — Node for Max script (the glue)
- `package.json` — declares `mouthflow.js` as main (no external deps)

## Use it
1. In Live, drag **`Mouthflow.amxd`** onto a track (it's an audio-effect device).
2. The device panel opens in Presentation with: **duration (s)** (default 8),
   **count-in (s)** (default 3), a **hint** field, a **Generate** button, and a
   **status** line.
3. Type a hint (e.g. "punchy 808 trap"), set duration, click **Generate** →
   count-in → beatbox → a new track with a kit + clip lands in Live, tempo
   matched. The status line tracks `recording → transcribing → plan → done`.

The panel drives the same `uv run mouthflow record` path as the CLI; it reads
your key from the repo `.env` and targets `~/UhnTiss/uhntiss` by default
(override with a `repo <path>` / `uv <path>` message to `node.script`).

## How it's wired (reference — regenerate, don't hand-edit)
The `.amxd` is generated programmatically (an `ampf` container wrapping the
maxpat JSON), so edit the generator rather than wiring by hand. Layout:
`generate` message + `duration`/`countin` numbers + `hint` textedit →
`prepend …` → `node.script`; `node.script` outlet → `route status` →
`prepend set` → status message; a `loadbang` seeds the 8/3 defaults.

<details><summary>Manual build (if you ever rebuild in the Max editor)</summary>

1. In Live: **Create → Max Audio Effect** (or drag an empty *Max Audio Effect*
   onto a track), then click **Edit** (the pencil) to open the Max editor.
2. Add the brain: an object box `node.script mouthflow.js @autostart 1`.
3. Add the controls (Live UI objects) and wire each into `node.script`:
   | Control | Object | Wire to node.script via |
   |---|---|---|
   | Generate | `live.text` (Mode: Button) | `t generate` |
   | List kits | `live.text` (Mode: Button) | `t list_kits` |
   | Duration (s) | `live.numbox` | `prepend duration` |
   | Count-in (s) | `live.numbox` (default 3) | `prepend countin` |
   | Hint | `textedit` | `prepend hint` |
   | Kit | `live.menu` — selection outlet | `prepend kit_index` |
4. Add config (so paths are explicit/portable): a `loadbang` →
   two message boxes → `node.script`:
   - `repo /Users/rene/UhnTiss/uhntiss`
   - `uv /Users/rene/.local/bin/uv`
   (The script defaults to these, so this is optional but recommended.)
5. Wire the **node.script outlet** → `route status busy tempo rationale done error kitmenu`
   and distribute:
   | Branch | Goes to |
   |---|---|
   | `status` | `prepend set` → a **message** box (the progress line) |
   | `tempo` | a `live.numbox`/display |
   | `rationale` | `prepend set` → a message/comment box |
   | `busy` | a LED / use to disable the Generate button while 1 |
   | `kitmenu` | straight into the `live.menu` (it sends `clear` / `append <name>`) |
   | `done` / `error` | optional status LED / message |
6. **Save** the device into this `m4l/` folder (e.g. `Mouthflow.amxd`).

</details>

The current prebuilt panel wires Generate/duration/count-in/hint/status. The
`list_kits` / kit-menu handlers exist in `mouthflow.js` but aren't on the panel
yet (a kit dropdown is the next addition).

## Message reference (inlet → script)
`repo <path>` · `uv <path>` · `duration <s>` · `countin <s>` · `hint <text…>` ·
`list_kits` · `kit_index <i>` · `kit_uri <uri>` · `generate` · `cancel`

## Outlet reference (script → patch)
`status <text>` · `busy <0|1>` · `tempo <bpm>` · `rationale <text>` ·
`done <0|1>` · `error <text>` · `kitmenu clear|append <name>`

## Notes / known limits (MVP)
- Audio is captured by the CLI (`sounddevice`, system default mic), not through
  Live's audio engine — so arm/monitor state in Live doesn't affect it yet.
- GUI-spawned processes don't inherit your shell `PATH`; that's why the script
  calls `uv` by absolute path and reads the key from the repo `.env`.
- The kit list reflects `list-kits`, which currently includes one-shot samples
  as well as racks (see the kit-discovery filtering follow-up).
