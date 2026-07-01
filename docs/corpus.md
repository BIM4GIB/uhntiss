# Corpus labelling convention

> **Current size:** two tracked trios (`01_boombap_mimic`, `02_bb100`) — small,
> and a known limitation. The historical spec's N=20 tables are *targets*, not
> achieved coverage. Per-user drum ground truth additionally comes from mimic
> takes (see [`../mimic/README.md`](../mimic/README.md)).

Each drum clip lives in `tests/fixtures/clips/` as a trio:

```
NN_slug.wav    # the beatbox recording — 44.1 kHz, 16-bit, mono
NN_slug.mid    # the drums you *meant*, hand-placed in Ableton, GM drum map (ch 10)
NN_slug.json   # metadata
```

## `NN_slug.json`

```json
{
  "tempo": 92,
  "style": "boom-bap",
  "notes": "Loose swing, unstressed snare on the & of 3. Hats quiet."
}
```

Fields:
- `tempo` — BPM. The tempo you were *going for*, not what librosa detects.
- `style` — free-form short label (`boom-bap`, `trap`, `dnb`, `breakbeat`, `four-on-the-floor`, ...).
- `notes` — any performance quirks worth remembering. Honest is best.

## Coverage targets

Aim for variety across the corpus as a whole:
- Tempos 70–160 BPM.
- Multiple styles.
- A mix of clean and sloppy performances (sloppy is more realistic).
- At least a few with non-standard patterns (triplets, half-time, fills).

## Adding a new clip

1. Record the WAV (`mouthflow record`, or any DAW).
2. Hand-place MIDI in Ableton against the audio until it matches your
   intent, export as `NN_slug.mid`.
3. Fill `NN_slug.json`.
4. Commit all three files together.

## Pitched (bass / lead) references

Pitched ground truth uses a different convention: a reference melody grid
`mimic/<name>.notegrid.json` —

```json
{"bpm": 100, "notes": [[0.0, 40], [0.6, 47], [1.2, 52]]}
```

(`notes` = `[time_s, midi]` pairs) — scored against a recorded take
`mimic/<name>.hum.wav` by `eval/note_eval.py` (note P/R/F1 + octave-error,
onset-aligned). No real tonal takes are recorded yet; that is the pending
next step for tuning bass/lead on real voice.
