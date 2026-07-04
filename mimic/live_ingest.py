"""Live-native dataset recording: reference MIDI clips + audio takes, side by side.

The flow the performer asked for — no headphmones-playrec, no terminal:

1. ``setup`` creates "MF Ref …" MIDI track(s) in the OPEN Live set, one named
   clip per plan row (the riff as real MIDI, playable by any instrument you
   drop on the track). Add ONE audio track next to them for your takes.
2. In Live: launch a reference clip, listen, then session-record your vocal
   imitation into the audio track (metronome/count-in as you like). The pairs
   sit side by side in the session grid.
3. Select your take clip and ``ingest`` (the panels' `data_ingest` button):
   the take is fetched via the bridge (``get_selected_clip``), the reference
   grid is rebuilt in seconds at the CURRENT project tempo (record at any
   tempo you like — the actual tempo is captured), scored, gated, and
   ingested into ``datasets/`` exactly like the playrec flow.

Row order: `next`/`ingest` walk the plan in order; the status line always
says which reference to play next. Record 1–2 loops per take; keep the song
tempo unchanged between recording a take and ingesting it.

  uv run python -m mimic.live_ingest setup  --plan starter_bass
  uv run python -m mimic.live_ingest ingest --plan starter_bass [--row b03]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mimic import tonal
from mimic.session import PLANS, _load_progress, _next_row, _progress_str, _save_progress
from mouthflow.execute import AbletonClient, AbletonError

_LOOP_BEATS = 8.0  # riffs are written over 2 bars of 4/4
_SLOTS_PER_TRACK = 8  # stock Live sets ship 8 scenes; chunk tracks to fit
# Takes recorded inside Live can carry count-in / pre-roll before the
# performance; search alignment further ahead than the mic flow's window.
_LIVE_OFFSET_RANGE = (-0.25, 4.0)


def _row_notes(voice: str, riff: str, key: str) -> list[dict]:
    """Plan row -> ableton-mcp note dicts (beats), riff anchored at beat 0."""
    root = tonal._root_midi(voice, key)
    return [
        {
            "pitch": root + degree,
            "start_time": float(start_b),
            "duration": max(0.1, float(dur_b)),
            "velocity": 100,
            "mute": False,
        }
        for start_b, dur_b, degree in tonal.RIFFS[voice][riff]
    ]


def _connect(host: str, port: int) -> AbletonClient:
    client = AbletonClient(host, port)
    try:
        client.connect()
    except OSError as exc:
        raise SystemExit(
            f"Ableton not reachable at {host}:{port} ({exc}) — open Live with the "
            "AbletonMCP control surface enabled, then retry"
        )
    return client


def setup(plan: str, host: str, port: int) -> None:
    rows = PLANS[plan]
    chunks = [rows[i : i + _SLOTS_PER_TRACK] for i in range(0, len(rows), _SLOTS_PER_TRACK)]
    with _connect(host, port) as client:
        made = 0
        for c_idx, chunk in enumerate(chunks):
            suffix = f" {c_idx + 1}" if len(chunks) > 1 else ""
            track = client.create_midi_track(f"MF Ref {plan}{suffix}")
            for slot, (name, voice, riff, key, bpm, role) in enumerate(chunk):
                try:
                    client.send_command(
                        "create_clip",
                        {"track_index": track, "clip_index": slot, "length": _LOOP_BEATS},
                    )
                    client.send_command(
                        "add_notes_to_clip",
                        {"track_index": track, "clip_index": slot,
                         "notes": _row_notes(voice, riff, key)},
                    )
                    client.send_command(
                        "set_clip_name",
                        {"track_index": track, "clip_index": slot,
                         "name": f"{name} {riff} {key} ({role})"},
                    )
                    made += 1
                except AbletonError as exc:
                    print(f"could not create clip {name} (slot {slot}): {exc}")
                    print("if this is a scene-count problem, add scenes in Live and rerun")
                    break
    print(f"created {made}/{len(rows)} reference clips on {len(chunks)} track(s)")
    print("now: drop an instrument on the MF Ref track(s), add ONE audio track for")
    print("your takes, launch a reference, record your imitation next to it, select")
    print("the take clip, and hit the panel's data_ingest (order shown by data_next)")


def ingest(plan: str, row_id: str | None, host: str, port: int, force: bool = False) -> int:
    progress = _load_progress()
    if row_id:
        matches = [r for r in PLANS[plan] if r[0] == row_id]
        if not matches:
            print(f"no row {row_id!r} in {plan}")
            return 1
        row = matches[0]
    else:
        row = _next_row(plan, progress)
        if row is None:
            print(f"{plan} COMPLETE ({_progress_str(plan, progress)}) — nothing to ingest")
            return 0
    name, voice, riff, key, bpm_nominal, role = row

    with _connect(host, port) as client:
        try:
            info = client.get_selected_clip()
        except AbletonError as exc:
            print(f"could not read the selected clip ({exc}) — select your TAKE clip in Live")
            return 1
        path = info.get("file_path") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("is_audio") and path):
            print("select the AUDIO take clip in Live's detail view first")
            return 1
        session = client.get_session_info()
        tempo = float(session.get("tempo", bpm_nominal)) if isinstance(session, dict) else bpm_nominal

    # Rebuild the reference grid in seconds at the tempo the take was
    # actually recorded at (= current project tempo — don't change it between
    # recording and ingesting).
    beat = 60.0 / tempo
    grid_notes = []
    durs = []
    root = tonal._root_midi(voice, key)
    for start_b, dur_b, degree in tonal.RIFFS[voice][riff]:
        grid_notes.append([round(start_b * beat, 4), root + degree])
        durs.append(round(max(0.1, dur_b * beat), 3))

    p = tonal._paths(name)
    p["grid"].parent.mkdir(parents=True, exist_ok=True)
    json.dump(
        {"voice": voice, "riff": riff, "key": key, "bpm": tempo, "loops": 1,
         "source": "live", "notes": grid_notes, "durs": durs},
        open(p["grid"], "w"),
    )
    shutil.copy2(path, p["take"])
    # No synthesized reference file in this flow — the reference IS the Live
    # clip (its MIDI is reproducible from the grid json).
    p["reference"].unlink(missing_ok=True)
    print(f"ingesting {Path(path).name} as {name} · {voice}/{riff} {key} @ {tempo:g}")

    ok = tonal.score(name, role=role, force=force, offset_range=_LIVE_OFFSET_RANGE)
    if ok:
        progress = _load_progress()
        progress.setdefault(plan, []).append(name)
        progress["_last"] = {"plan": plan, "name": name, "role": role}
        _save_progress(progress)
        nxt = _next_row(plan, _load_progress())
        print(f"PASS · {_progress_str(plan, _load_progress())} done"
              + (f" · next: play '{nxt[0]} {nxt[2]} {nxt[3]}'" if nxt else " · PLAN COMPLETE"))
    else:
        progress["_last"] = {"plan": plan, "name": name, "role": role}
        _save_progress(progress)
        print("RETRY · re-record the take (or data_keep to accept it anyway)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "ingest"])
    ap.add_argument("--plan", required=True, choices=list(PLANS))
    ap.add_argument("--row", default=None, help="plan row id (default: next pending)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "setup":
        setup(a.plan, a.host, a.port)
    else:
        raise SystemExit(ingest(a.plan, a.row, a.host, a.port, force=a.force))


if __name__ == "__main__":
    main()
