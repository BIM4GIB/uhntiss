# mimic/ — ground-truth take harness

Reference-generation + record + score loop for collecting *labelled* beatbox
takes. A reference loop with a known grid is synthesised, the user mimics it
into the mic while it plays, and because playback and recording are
sample-aligned the grid **is** the ground truth for the take. These takes are
the training and evaluation data for the drum classifier.

Everything is driven by [take.py](take.py):

```
uv run python -m mimic.take gen   --name bb100 --bpm 100 --preset boombap
uv run python -m mimic.take rec   --name bb100            # play + record, sample-aligned
uv run python -m mimic.take score --name bb100 --clip 02_bb100
uv run python -m mimic.take devices                        # pick a real mic with rec --input <N>
```

`gen` synthesises a count-in bar plus a preset pattern (`boombap`,
`snareheavy`, `fourfloor` — one sound per 8th slot, so it is mimicable by
mouth). `rec` uses `sounddevice.playrec` and warns when the take is too quiet
or clipping — the classifier learns the timbre *at the recorded level*, so
record at your real gain/distance. `score` searches a constrained
reaction-latency window (0.12–0.30 s; a free search can lock onto an
off-by-one-slot offset), reports model-vs-heuristic accuracy, and copies the
take out as a corpus clip under `tests/fixtures/clips/` (wav + ground-truth
MIDI + metadata json).

## File conventions

| File | Written by | Format |
| --- | --- | --- |
| `<name>.grid.json` | `gen` (or by hand for in-Live takes) | `{"bpm": N, "grid": [[time_s, label], ...]}` with labels `kick`/`snare`/`hat` |
| `<name>.reference.wav` | `gen` | the synthesised loop the user mimics |
| `<name>.mimic.wav` | `rec` (or exported from Live) | the recorded take, mono 44.1 kHz PCM_16 |
| `<name>.labeled.json` | `score` | legacy per-onset label dump; training no longer reads it |
| `<name>.notegrid.json` | by hand (tonal) | `{"bpm": N, "notes": [[time_s, midi], ...]}` — consumed by [eval/note_eval.py](../eval/note_eval.py) alongside `<name>.hum.wav` |

Two take families exist:

- `bb84` / `bb100` — full `gen`→`rec` trios recorded with the harness.
- `live_*` — six takes recorded *inside Ableton* (transport-synced,
  side-by-side with the reference at 100 BPM), with tempo-matched grids
  committed separately. No `.reference.wav`; the grid + wav pair is enough.

## How takes feed training

[eval/featurelab.py](../eval/featurelab.py) `labeled_onsets()` globs every
`*.grid.json` here, loads the matching `.mimic.wav`, detects onsets, and
labels each onset from the grid. Alignment picks the constant offset
(−0.6…0.6 s) that maximises **timbre agreement** — matched onsets whose
heuristic class equals the grid label — not raw match count. This matters:
raw rhythm matching is half-beat phase-ambiguous on dense hat grids (it
slides kicks onto hat slots), and it silently mislabelled the first in-Live
batch when the grids were built at 90 BPM but recorded at 100 (LOO accuracy
0.34 until the tempo-matched grids + timbre alignment landed; 0.81 after).
Grid tempo must match the take's record tempo for the alignment to lock.

[eval/train_classifier.py](../eval/train_classifier.py) re-extracts the 10
`drum_features` from those labelled onsets (plus the `calibration/` one-shots)
and writes the k-NN model to `mouthflow/drum_model.json`; new takes dropped in
here are picked up automatically. [eval/classifier_cv.py](../eval/classifier_cv.py)
gives the honest numbers (current data: LOO ~0.81, per-take held-out mean ~0.73).

## Current contents / gaps

- Drum takes: `bb84`, `bb100` (harness trios) + `live_1`…`live_6` (in-Live,
  grid + wav only) — 8 takes total. Corpus clips exported so far:
  `01_boombap_mimic`, `02_bb100`.
- **No tonal takes yet**: there are no `*.notegrid.json` / `*.hum.wav` pairs,
  so `eval/note_eval.py` currently only runs its synthetic sine self-test.
  A `gen`-style tonal reference (melody + notegrid) is the missing piece for
  measuring bass/lead on real humming.
