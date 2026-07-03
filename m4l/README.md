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
- `Mouthflow.amxd` — **the prebuilt drums device** (drop it on a track and use it)
- `MouthflowBass.amxd` / `MouthflowLead.amxd` / `MouthflowDrone.amxd` — per-voice
  panels (generated; see below)
- `mouthflow.js` — Node for Max glue (template); each panel loads a per-voice
  copy `mouthflow_<voice>.js` with the voice baked in
- `generate.py` — regenerates the per-voice panels + glue from `Mouthflow.amxd`
- `package.json` — declares `mouthflow.js` as main (no external deps)

## Per-voice panels (bass / lead / drone)
Each generated panel points its `node.script` at a per-voice glue copy
(`mouthflow_<voice>.js`) whose `device` **default is baked in** — deliberately
NOT a loadbang-driven `device <id>` message, because Node for Max starts
asynchronously and a loadbang message races (and usually loses to) script
startup. The drums panel keeps plain `mouthflow.js` (defaults to `drums`).

The pitched panels also get **injected controls** (no Max editing needed):
**Transcribe Clip**, **record_start / record_stop** (open-ended takes via
`record-stream` — start, perform as long as you like, stop), and
**bars / correct / key / scale** fields feeding the note-correction + bar-fit
flags. The `correct` toggle defaults ON.

Regenerate and install with:

```bash
python m4l/generate.py            # writes MouthflowBass/Lead/Drone.amxd + glue + self-check
python m4l/generate.py --install  # …and syncs panels+glue into ~/Music/Ableton/User Library/Devices
```

> **`--install` matters:** Live loads devices from `User Library/Devices`, and
> `node.script` resolves the glue **next to the .amxd** — both files must land
> there (the flag backs up what it replaces as `.bak`). A fresh browser drag
> loads the current file; no Live restart needed for panel changes.
>
> **Layout rule (learned the hard way):** Live's device strip is fixed-height
> (~196 px) and *silently clips* anything laid out below — new controls must go
> in a **column to the right**, never stacked underneath. The generator does
> this; verified rendering on-screen in Live 12.
>
> The generator reproduces the `.amxd` container exactly (self-checked) and
> validates every patchline endpoint, but it can't open Max — smoke-test a
> regenerated panel once (drag onto a track, click Generate). You can also
> drive any voice from the drums panel by sending it a `device bass` (etc.)
> message.

The bass/lead/drone instrument categories are confirmed at runtime (the kit
dropdown shells out to `mouthflow list-kits --device <id>`); see the
device-discovery note in the main repo handover.

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
   | Tempo (BPM) | `live.numbox` (0 = auto) | `prepend tempo` |
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

The prebuilt panel wires Generate / duration / count-in / hint / **kit dropdown
+ List Kits** / status.

> **Kit dropdown caveat:** the **List Kits** button shells out to
> `mouthflow list-kits --device <voice>` on the repo the device targets
> (default `~/UhnTiss/uhntiss`); Live must be reachable for it to populate.
> Generate works without it (the planner picks from the hint). The list still
> mixes one-shot samples with real racks (known kit-discovery pollution).

## Message reference (inlet → script)
`repo <path>` · `uv <path>` · `device <id>` · `duration <s>` · `countin <s>` ·
`tempo <bpm>` · `hint <text…>` · `list_kits` · `kit_index <i>` · `kit_uri <uri>` ·
`list_inputs` · `input_index <i>` · `input <i>` · `file <path>` ·
`transcribe_clip` · `generate` · `bars <auto|off|1|2|4|8|16>` · `correct <0|1>` ·
`key <C|F#|…>` · `scale <major|minor|…>` · `record_start` · `record_stop` ·
`record <0|1>` · `cancel`

## Outlet reference (script → patch)
`status <text>` · `level <dBFS>` (live input meter while record-streaming;
the pitched panels show it in a number box next to the record buttons) ·
`busy <0|1>` · `tempo <bpm>` · `rationale <text>` ·
`done <0|1>` · `error <text>` · `kitmenu clear|append <name>` ·
`inputmenu clear|append <name>`

## Transcribe a clip (no re-recording)
The pitched panels ship a **Transcribe Clip** button (auto-injected by
`generate.py` — no Max editing needed). Select an **audio** clip in Live, click
it → the clip's sample is transcribed and a kit + MIDI clip lands, no mic
involved.

> `transcribe_clip` needs the forked `get_selected_clip` bridge command (see
> [`../bridge/`](../bridge/README.md)). Without it, you can still transcribe a
> file: send `file <absolute-path>` to `node.script`, or run
> `mouthflow run "<clip.aif>"` from a terminal.

An **Input** mic dropdown is not yet injected — to add one in the Max editor,
clone the Kit menu: `live.text` button → `t list_inputs`, `live.menu` →
`prepend input_index`, and wire the outlet's `inputmenu` route branch into the
menu (it sends `clear`/`append <name>`).

## Notes / known limits (MVP)
- Generate captures the CLI mic (`--input` selects which); **Transcribe Clip**
  reads the selected clip's file instead, bypassing the mic entirely.
- GUI-spawned processes don't inherit your shell `PATH`; that's why the script
  calls `uv` by absolute path and reads the key from the repo `.env`.
- The kit list reflects `list-kits`, which currently includes one-shot samples
  as well as racks (see the kit-discovery filtering follow-up).
