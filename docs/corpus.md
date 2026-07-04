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


## The voice dataset (2026-07-04 design)

Quality is procedurally improvable only against ground truth from THE
performer's voice. Every dataset row is a reference-linked pair: a synthesized
riff/pattern the performer hears in headphones and imitates, recorded
sample-synced (`sounddevice.playrec`) so **the reference grid IS the label**
for what they *meant*. No manual annotation, ever.

Harnesses: `mimic/take.py` (drums, existing) + `mimic/tonal.py` (bass/lead/
drone, riff archetypes stressing one failure mode each) + `mimic/session.py`
(guided runner: listen -> record -> auto-score -> auto-ingest, resumable).
Accepted takes land in `datasets/<voice>/` (gitignored — voice recordings stay
local) with a `manifest.jsonl` row (voice, riff, key, bpm, role, pipeline sha,
scores).

Every row is tagged with a **role**:

| role | purpose | rule |
|---|---|---|
| calibrate | fit thresholds to this performer (min_note_s, confidence gates, velocity anchors, octave snap) | tune freely |
| eval | the honest number; CI-gateable | recorded LAST, never tuned against |
| train | per-user models (k-NN today, embedding/prototype later) | grows over time |

### Starter counts (~3 sessions of 15–20 min)

| plan | takes | split | what it unlocks |
|---|---|---|---|
| `starter_bass` | 20 (3 archetypes x keys x 85–140 BPM) | 12 cal / 8 eval | bass threshold calibration + the pyin-vs-SwiftF0 A/B **with a number** |
| `starter_lead` | 10 | 6 cal / 4 eval | lead register + octave-continuity calibration |
| `starter_drone` | 5 | 5 cal | held-pitch/chord sanity |
| drums (`take.py`) | 20 (presets x 85/100/120/140 + freestyle) | 12 train / 8 eval | replaces the train-contaminated fixture corpus; honest CI class-acc |

Bass archetypes: `roots` (sustained pitch/octave stability), `pump8`
(same-pitch staccato 8ths — re-articulation + velocity), `funk` (syncopation,
rests, octave jump). Lead: `steps`, `arp`, `held`. Genre coverage enters
through drum patterns and tempo bands — extend `PRESETS`/`PLANS` per genre.

### Growth loop

1. Record a plan (or a few takes after any session — 2 min).
2. `eval/note_eval` + `run_eval` read `datasets/` -> per-voice honest F1.
3. Changes are accepted only if the eval-role numbers don't regress.
4. When enough `train` rows exist per voice: per-user recalibration, then the
   neural-ears A/B (SwiftF0 etc.) decided by the eval split, not vibes.

Upgrade path: render references through actual Live instruments via the
bridge (same grids, real target sounds) so imitation and instrument-matching
share one dataset.
