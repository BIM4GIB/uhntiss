# eval/ — measurement harness

Everything in this directory is deterministic and offline: no Claude calls, no
Ableton socket (the only script that involves Live is `baseline_ableton.py`,
and even that never talks to Live — it just manages a manual workflow). The rule from the drum-quality work
applies throughout: you can't improve what you can't measure — so every change
to detection, features, or quantisation should be run past the relevant gate
below before it lands.

## Scripts

| Script | What it measures | Run |
|---|---|---|
| [`run_eval.py`](run_eval.py) | End-to-end drum pipeline on the fixture corpus: onset F1 (50 ms tol), class accuracy, tempo ±3 BPM, vs. hand-placed `.mid` ground truth | `uv run python -m eval.run_eval` |
| [`classifier_cv.py`](classifier_cv.py) | Drum classifier generalisation: leave-one-out + held-out-take CV + confusion matrix over the production 10-feature set | `uv run python -m eval.classifier_cv` |
| [`onset_sanity.py`](onset_sanity.py) | Onset *detector* in isolation — raw (un-quantised) onsets vs. mimic reference grids, so tempo/phase errors don't fold into the score | `uv run python -m eval.onset_sanity` |
| [`note_eval.py`](note_eval.py) | Pitched voices (bass/lead): note P/R/F1 + octave-error rate vs. `mimic/<name>.notegrid.json`; ships a synthetic sine self-test | `uv run python -m eval.note_eval` |
| [`train_classifier.py`](train_classifier.py) | Trains the per-user k-NN drum model and writes [`mouthflow/drum_model.json`](../mouthflow/drum_model.json); prints the CV report first | `uv run python -m eval.train_classifier` |
| [`featurelab.py`](featurelab.py) | Feature-experiment harness: bake candidate featurizers off against the baseline on the honest held-out-take metric | `uv run python -m eval.featurelab` |
| [`timing_probe.py`](timing_probe.py) | Per-clip quantiser diagnosis: detected tempo + confidence, snap vs. raw decision, per-hit movement, swing estimate | `uv run python -m eval.timing_probe clip.wav [--tempo N]` |
| [`baseline_ableton.py`](baseline_ableton.py) | Manages the manual A/B baseline: which clips have a paired `.baseline.mid` from Ableton's Convert Drums, checklist for the rest | `uv run python -m eval.baseline_ableton` |
| [`taste_review.py`](taste_review.py) | Interactive blind 1–5 A/B rating (Mouthflow render vs. Ableton baseline render), appends to `eval/taste_review.csv` | `uv run python -m eval.taste_review` |

All commands run from the repo root. `uv run pytest` (75 passed as of
2026-07-01) is separate — unit tests, not accuracy measurement.

## Regression gates vs. exploratory

**Gates** — run these when touching the corresponding code; they have targets
or a single honest headline number:

- `run_eval` — the pipeline gate. Targets baked in: onset F1 ≥ 0.75, class
  accuracy ≥ 0.65, tempo within ±3 BPM on ≥ 80% of clips, timing MAE ≤ 45 ms.
  **Gated for real:** exits non-zero below target (CI runs it;
  `--report-only` disables). Also reports swing-preservation error and
  velocity rank-correlation (n/a until labelled takes with real dynamics
  exist), and prints a train-contamination warning on the default corpus.
- `classifier_cv` — the classifier gate. Held-out-take CV is the honest
  number; LOO flatters slightly.
- `onset_sanity` — the detector gate, tempo-independent. Use when changing
  onset detection.
- `note_eval` — *intended* as the pitched gate, but see below.

**Exploratory / manual** — `featurelab` (feature experiments), `timing_probe`
(one clip at a time, for "the drums feel off" complaints), `train_classifier`
(training, not scoring — though it prints the CV report), and the
`baseline_ableton` + `taste_review` pair (requires manual per-clip work in
Live plus a human rater; results in `taste_review.csv`).

## Current numbers (as of 2026-07-01)

| Metric | Value | Target |
|---|---|---|
| Pipeline onset F1 (`run_eval`, N=2 clips) | 0.87 | ≥ 0.75 |
| Pipeline drum-class accuracy | 0.95 | ≥ 0.65 |
| Tempo within ±3 BPM | 2/2 | ≥ 80% |
| Classifier LOO accuracy (`classifier_cv`, 260 labelled onsets) | 0.81 | — |
| Classifier held-out-take mean (8 takes) | 0.73 | — |
| Raw onset-detector macro F1 (`onset_sanity`, 8 takes) | 0.56 | — |
| Pitched self-test, bass (clean sines) | F1 1.00 | ~1.0 |
| Pitched self-test, lead (clean sines) | F1 0.00 (5/8 octave errors) | ~1.0 |

The held-out-take spread is wide (0.50–0.93 per take): the classifier is
per-user and the harder live takes (snare-heavy, uptempo) drag the mean down.
The lead self-test failure is real and unfixed — the transcriber lands the
right pitch class but the wrong octave on synthetic sines, most likely the
octave-snap into `LEAD_CONFIG`'s range. Bass, the primary pitched voice,
passes clean.

## Corpus — small, be honest about it

- **Fixture trios** ([`tests/fixtures/clips/`](../tests/fixtures/clips/), see
  [`docs/corpus.md`](../docs/corpus.md)): only **2** tracked
  wav + mid + json trios (`01_boombap_mimic`, `02_bb100`). N=2 means
  `run_eval` catches regressions, not small effects.
- **Mimic takes** ([`mimic/`](../mimic/)): 8 beatbox takes with exact
  reference grids (`bb84`, `bb100`, six `live_*` takes) — the training and CV
  data for the drum classifier, recorded via `mimic/take.py`.
- **Calibration one-shots** ([`calibration/`](../calibration/)): kick, snare,
  closed/open hat.

## Per-voice coverage

Drums is the **only** voice with a working regression oracle (ground-truth
MIDI + grids + targets). The pitched eval (`note_eval`) is a synthetic
self-test only: no `mimic/*.notegrid.json` tonal takes have been recorded yet
— that is the pending next step before bass/lead changes can be gated. Drone
has **no eval at all**.

## Training the drum classifier

`eval/train_classifier.py` re-derives labelled onsets from source audio
(calibration one-shots + mimic takes via their grids, through
`eval.featurelab.labeled_onsets`), extracts the same 10 features the runtime
uses (`mouthflow/devices/drum/features.py`), prints the honest CV report, and
writes the k-NN model to `mouthflow/drum_model.json` — which the runtime
classifier loads, falling back to heuristics when the file is absent. k-NN
rather than nearest-centroid because drum classes are multi-modal (a bright
slow hat and a darker fast one are both hats). New recorded takes are picked
up automatically on the next training run.
