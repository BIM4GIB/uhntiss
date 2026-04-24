# Corpus labelling convention

Each clip lives in `tests/fixtures/clips/` as a trio:

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

1. Record the WAV (see `mouthflow/capture.py` once implemented, or any DAW).
2. Hand-place MIDI in Ableton against the audio until it matches your
   intent, export as `NN_slug.mid`.
3. Fill `NN_slug.json`.
4. Commit all three files together.
