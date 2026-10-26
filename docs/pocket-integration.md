# Pocket ⇄ mouthflow — the contract between the two device lines

One project, two device lines, two teams of contributors (René with Codex on
the native side, René with Claude on the Python side). This page is the
agreement that lets both move without stepping on each other. Written
2026-10-24 on `feat/pocket-avp-enroll` (branched from PR #19, merged with
main + `feat/enroll`).

## Who owns what

| area | owner | path |
|---|---|---|
| Native engine, forests, features, Teach, state codec, Max external + patch | **Pocket** (Codex) | `native/pocket/`, `m4l/pocket/` |
| Offline pipeline, Python classifier, panels that spawn the CLI, enrollment, evals | **mouthflow** (Claude) | `mouthflow/`, `m4l/*.amxd` + `generate.py`, `eval/` |
| The two bridges below | shared, **changes need both to look** | `mouthflow/pocket.py`, `eval/avp_pocket.py`, `native/pocket/include/pocket/state.hpp::decodeProfile`, `external.cpp::profile`, `m4l/pocket/pocket-profile.js` |

Rule of thumb: Pocket owns the *definition* of its features, metric and
state; mouthflow owns the *data* (AVP, enrollment clips) and the *evals*.
If Pocket changes `pocket::model::scale`, the 17 features, or the profile
layout, `mouthflow/pocket.py` and `tests/test_pocket.py` must change in the
same PR — the Python replica is pinned to them.

## Current interfaces — Pocket 06, feature version 3, state v5

The original v4 **156-double payload remains accepted unchanged**, and the
filename on Max's search path remains `uhntiss_pocket_profile.json`.

1. `pocket_analyze <sr> [gate] [policy]` consumes mono float32. Its first four
   columns are **onset seconds**, factory label, velocity, **decision seconds**,
   followed by the original 17 features. `slices` and `enrollment` append 120
   features, three observed sample lengths, and the observed window end: 145
   columns. The end is not a dispatch timestamp. `enrollment` uses the same
   candidate policy and next-onset truncation as the v5 engine.
2. V4/quick Teach retains weighted 3-NN over `pocket::model::scale`. V5 keeps
   that live metric across 2–16 named voices, each with up to 30 examples.
   A separate five-neighbour personal noise vote requires four negatives.
   Rich noise rejection uses only 0–10 ms live; commit uses completed causal
   slices up to 80 ms. Commit classification is uniform 5-NN, scaled/centered
   on enrollment and quantized to signed Q4. Python and C++ replicas are tested.
3. V5 profile header: `[5, 3, N, earlyRejects, sampleRate, richRejects]`, then
   120 scales and 120 centers. Each voice occupies 1,440 doubles: note;
   24 ASCII name bytes; early count, late count, radius, two reserved zeros;
   30×17 early doubles; 30×120 signed bytes packed four per uint32-valued double.
   Early negatives follow (`earlyRejects×17`), then rich negatives
   (`richRejects×31`: complete-slice mask 1/3/7 plus 30 packed words).
   Current export keeps full-window negatives (mask 7). Counts are at most 30.
   Maximum payload: 24,726 doubles. State adds `[5, anchor, eventCount]` and
   five values per event `(beat, velocity, built-in drum, voice index, MIDI note)`;
   maximum state: 27,289 doubles. Invalid imports fail transactionally.
4. `profile <numbers>` accepts v4 or v5; `profile_loaded` comes only after the
   audio thread applies it. PROFILE's `msg_int` is public (the earlier `.local`
   flag made the button inert). JSON writing is atomic; JS errors are visible.
   Exported named sounds retain their MIDI mappings; `noise` and `--reject-clip`
   train rejection, never a playable voice. Quick Teach does not train 80 ms
   features. Re-export after using it to restore the delayed model.
5. Audio-effect MIDI is routed with `live.routing` and `midiout` to a selected
   track. External notes use a vector-boundary high-priority clock, not the UI
   poll. Internal synthesis and stored timestamps remain sample-exact.
   Routing reference: [Cycling ’74 live.routing](https://docs.cycling74.com/legacy/max8/refpages/live.routing).

## Pocket 07 reliability candidate — 2026-10-26

Feature version **4**, state **v5** (same wire layout and capacity). The 17
positive features, their scale and the 120 late features are unchanged. Import
accepts feature-3 v5 banks and preserves their feature tag and old decisions;
156-double v4 remains exact. Re-export for the new behavior. The Max filename
is still `uhntiss_pocket_profile.json`; native/JS resources are versioned `07`.
The loader includes the independently necessary chunk-read and routing startup
fixes from the still-separate 06.1 host work.

Two changes are retained:

- A rejected personal candidate restores the **previous accepted onset's**
  35 ms exclusion window. It no longer reserves its own window. Accepted hits
  still use 35 ms separation, the same onset/velocity, and a 10 ms audible decision.
- An additional personal noise veto uses only the first 10 ms. At import on
  the message thread, a fixed-size equal-prior discriminant is derived from
  the decoded Q4 bank: pooled within-class covariance, 0.9 covariance + 0.1
  diagonal (floor 0.01) + 0.01 identity. DSP evaluates cached 40-feature linear
  scores. A negative must beat every positive strictly, and factory-positive
  attacks retain their old protection. This is an extra veto, not a new onset
  source or a late audible decision.

Full (mask 7) negatives keep their previous selection priority. Spare bank slots
may contain masks 1/3 with a complete first slice; these only train the new veto.
They cannot enter either legacy sustained-negative vote or the late loop veto.
Fewer than four sustained negatives retain the early 17-feature fallback. Import
and pattr restore rebuild the cache outside DSP; nothing new is serialized.
`pocket_profile --with-drum` carries the actual candidate's factory label for
native/Python parity; its old input mode remains available for legacy probes.

Actual Engine replay (`eval.pocket_reliability`, four approved calibration clips,
one hum trains and the other tests; no mixed-take fitting). These are development
source holdouts that have informed engineering, not untouched final tests:

| Noise enrollment → test | Live false/min before → after | Stored false/min before → after |
|---|---:|---:|
| b01 → b02 | 5.7 → 5.7 | 0 → 0 |
| b02 → b01 | 99.8 → 4.3 | 95.4 → 4.3 |

Across all eight real drum takes (271 references), the b01-enrolled profile's
live correct counts are **147 → 151**, **135 → 139**, **131 → 133** at
1/.5/.25 gain. Extras remain **52/36/26**. Newly recovered attacks include
wrong voice choices at reduced gain: wrong counts are 44→44, 48→49, 61→62.
Stored correct counts are 169→173, 156→161 and 169→170. These are personal-profile
results, not the stronger factory regression scores from earlier releases.
All 1,817 paired previously-correct event checks across both noise folds retain
their core voice, detected onset (within 1.01 samples) and velocity.
The permanently reserved uptempo take improves **25→26/37 live** and
**26→27/37 stored** at normal gain, with five extras unchanged. Half/quarter
accuracy is unchanged. It never fits the profile or a new model.

The unchanged-onset AVP classifier table below was rerun at both rates for
feature 4. Actual runtime is now separately measurable with
`eval.avp_pocket --enrollment --runtime`; restoring the exclusion window can
change which later candidates reach classification, so the same-onset table
alone is insufficient evidence of waveform behavior. At 48 kHz, actual Engine
three-voice live/stored correct counts are 987→987 / 1052→1054 out of 1,433
annotations; accepted matches 1,336→1,340 and extras 289→290. With four voices,
correct counts are 983→984 / 1042→1046; accepted matches 1,335→1,343 and extras
285→286. This small extra-trigger increase across AVP is disclosed separately
from the unchanged extras in the performer's eight-take audit.

**Release blocked:** per-attack-held-out, overlapping 24-hit roll montages at
50/75/100 ms spacing remain poor (only 1/1/5 correct at full level), while
140 ms spacing scores 24/24, including the alternating quarter-level ghost
case. The roll snippets were excluded with ±200 ms margins before fitting
that separate profile. They are constructed stress tests, not fresh human rolls.
A broader onset policy added many extras and was discarded; overlap enrollment
augmentation also failed to solve the gap and is not shipped. Humming is not
human breath/speech. Fresh independent recordings are still required; see
[the exact recording sheet](pocket07-calibration.md). Pocket 07 is a review
candidate and has not replaced the installed Pocket 06.1.

Reports: `native/pocket/reliability-48k.json`, `reliability-negatives.json`,
`reliability-avp-48000.json`, `reliability-avp-44100.json` and
`reliability-benchmark.json`. Factory model bytes remain unchanged.

## Current measurements (2026-10-25)

**Timestamp correction:** earlier evaluations matched/extracted at CSV column 3,
which is the decision time, approximately 10 ms after the detected onset.
Both AVP evaluators now use column 0. Historical tables below are retained as
research history, not the shipping result. The corrected untruncated window
study at 48 kHz is 73.0 → 78.8%; it is not a measurement of this bounded runtime.

| `eval.avp_pocket` protocol | 44.1 kHz | 48 kHz |
|---|---:|---:|
| factory:rep | 52.6 | 52.1 |
| factory:improv | 37.8 | 37.5 |
| factory:all | 47.6 | 47.2 |
| teach3-first | 71.7 | 71.8 |
| teach3-medoid | 70.8 | 70.6 |
| profile30, original metric | 72.6 | 73.1 |
| all123-knn, same onsets | 83.1 | 83.7 |
| v5, 3 voices, live10 | 70.0 | 70.5 |
| v5, 3 voices, loop80 | 74.5 | 75.2 |
| v5, 4 voices, live10 (core K/S/H) | 69.8 | 70.2 |
| v5, 4 voices, loop80 (core K/S/H) | 75.2 | 74.2 |

The v5 protocol trains on **whole one-class repetition clips**, including
unmatched detections, and tests only the same performer's improvisations.
Matched rejections count as wrong. Scales, radii, medoids and quantization never
use test audio. The older Teach rows filter training hits through annotation
matches, so compare live10 against loop80 within the v5 protocol.

At 48 kHz, 1,398/1,433 annotations match candidates; accepting/rematching the
three-voice live decisions retains 1,336, with 289 extra accepted candidates.
At 44.1 kHz those counts are 1,394, 1,315 and 239. Window truncations are 419/1,715
and 120/1,663 candidates respectively. Rematching accepted candidates can choose
a neighbouring onset, so rejected counts on the original match are a separate
measure. All 3,378 improvisation candidate predictions per vocabulary agree
between native and Python. See `native/pocket/enrollment-avp-*.json` for denominators.
The fourth class is not free at 48 kHz; no universal zero-cost claim is justified.

Personal noise audit (four isolated calibration classes; one hum trains, the
other tests; no held-out drum enrollment):

| negative enrollment → test | live false/min, before → after | loop false/min after |
|---|---:|---:|
| b01 → b02 | 34.3 → 5.7 | 0.0 |
| b02 → b01 | 112.8 → 99.8 | 95.4 |

Neither fold adds a missed annotated drum hit on the two audited fixtures:
51 matched hits retained out of 62 annotations, for both live and loop decisions.
That is a small, signal-assisted audit, not independent human ground truth.
Humming does not validate speech or breath. Partial negative fragments were
excluded from the rich bank after a development probe suppressed real kicks;
these clips therefore are development holdouts, not an untouched final test.
The permanently reserved uptempo take and mislabeled CAL 48k 0003 are excluded
from all profile fitting. Stage qualification still requires fresh one-class
calibration and independent breath/speech/expert-roll takes.

The maximum-bank benchmark (16×30 positives +30 negatives, 512-event playback,
concurrent recording, 64-sample blocks) measured worst blocks of 0.48/0.34 ms at
48/96 kHz on this Mac (budgets 1.33/0.67 ms) after model prefaulting in DSP setup. It is a local CPU
measurement, not a round-trip latency or host dropout guarantee.

## Historical research and original asks

## What the numbers say (AVP, 25 performers, `eval/avp_pocket.py`, 2026-10-24)

Pocket's detector finds 92.6 % of annotated hits (44.1 k; 93.3 % at 48 k)
with 0.08 extra triggers per hit (0.12 at 48 k). On the matched hits:

| protocol | 44.1 k | 48 k | note |
|---|---|---|---|
| factory forests, other voices | 47.7 (k 9 s 15 h 91) | 47.2 | **does not transfer** — calls nearly everything a hat |
| factory, improvisations only | 37.9 | 37.5 | |
| Teach as shipped (first 3 hits/voice) | 71.7 | 71.8 | |
| Teach from medoids (`export-pocket`) | 70.7 | 70.6 | same within noise (n≈1350, ±1.2) |
| hypothetical 30 examples/voice, same metric | 72.5 | 73.2 | a bigger Profile buys ~1 pt |
| mouthflow `all123` k-NN, **same onsets, same enrollment hits** | 81.1 | 81.7 | the feature set/metric is the gap, not the data |

Reading: Pocket's Teach works (72 % from 9 examples) and is where its
cross-performer accuracy comes from; the factory forests are one-performer
models. Enlarging the Profile is not the lever — the 17-feature metric
saturates around 72–73 % while the Python features reach 82 % on the very
same detections. The next cross-team decision is therefore about the
**feature vector** (can a 10 ms causal window carry more of what `all123`
carries?), not the profile size.

## Window study: the ceiling is the 10 ms window, not the metric (`eval/avp_window.py`, 2026-10-24)

Same 3,996 Pocket-detected onsets, same enrollment protocol (that
performer's repetition hits → k-NN → their improvisation), one feature
family (level-normalised log-mel + MFCC + zcr + centroid + 30 ms
pre-context ratio) over **causal** windows starting at the detection:

| features | window | acc (k · s · h) |
|---|---|---|
| Pocket's own 17, through the same k-NN | 10 ms (+30 ms context) | 72.1 (78 · 54 · 79) |
| richer family | 10 ms | 73.6 (75 · 56 · 86) |
| richer family | 20 ms | 75.4 |
| richer family | 40 ms | 77.7 |
| richer family | 80 ms | 79.6 (87 · 56 · 90) |
| richer family, one 200 ms window | 200 ms | 74.2 (the attack gets diluted) |
| **3 causal slices 0–10 / 10–40 / 40–80 ms** | **80 ms** | **80.3 (84 · 63 · 90)** |
| 3 slices 0–10 / 10–40 / 40–120 ms | 120 ms | 81.2 (84 · 67 · 89) |
| `all123` (mouthflow, non-causal design) | ≤200 ms | 81.1 (85 · 65 · 89) |

Teach-sized (3 examples per voice, 3-NN, z-scored): Pocket 17 → 67.5,
richer 10 ms → 69.0, **3 slices to 80 ms → 76.4** (k 85 · s 80 · h 64).
(Pocket's native scale-normalised metric does better than z-scoring from
nine examples — 71.7 in `avp_pocket` — so treat the Teach-sized rows as a
ranking, not absolute; the gap is +7–9 either way.)

**Reading.** Replacing the metric changes nothing (72.1 vs 72.5). Richer
features inside 10 ms buy 1.5 points. Every doubling of the causal window
buys ~2 points, and a 3-slice attack/body/tail layout at 80 ms recovers
snare (54 → 63) and reaches the non-causal `all123` number. The 10 ms
decision is the ceiling.

**Recommendation to Pocket (Codex's call):** keep the 10 ms decision for
what the performer *hears* (provisional voice, as now), and re-decide at
80 ms with the 3-slice causal vector for what the *loop stores* — a
delayed decision for the commit path, in the lineage of Stowell's
"delayed decision-making in real-time beatbox classification". The audible
hit stays at 10 ms; the recorded loop gets +8 points across performers.
If a re-label must also correct the sounding voice, that is a voice-steal/
crossfade question on the synth side, not a classifier one.

## Two asks for Pocket from the product bar (2026-10-25)

The bar is "something Beardyman would use": a virtuoso with twenty
repeatable sounds, sung basslines, an open mic on stage.

1. **N voices, not 3.** mouthflow enrollment now takes any user-named
   sound mapped to a MIDI note (`enroll clip rimshot --note 37`); the k-NN
   emits it and the planner sees the performer's name for it. Pocket's
   `Profile` is `examples[3][3]`, so `export-pocket` can only hand over
   kick/snare/hat and reports the rest as skipped. Evidence that more
   classes don't cost the core three: on AVP a 4-class per-user k-NN (open
   hat split out) scores identically on kick/snare/hat (83.4 %) and
   separates open from closed hats at 72–87 %. A Profile of N voices × k
   examples (k = 3 still fine) is the native change; the state blob needs a
   version bump.
2. **Rolls bound the delayed decision.** A pro rolls at 50–100 ms per hit.
   The 80 ms re-decision recommended above must truncate its window at the
   next detected onset (the 40–80 ms slice would otherwise contain the next
   hit); the 0–10/10–40 slices alone scored 76.9 % on AVP, so a truncated
   window degrades gracefully rather than failing. AVP amateurs rarely roll
   — this needs René's or a pro's roll takes to measure.

## The negative set: open-mic rejection (`eval/negatives.py`, 2026-10-25)

Baseline on hummed bass takes (singing, no drums): mouthflow drums device
**72 false hits/min**, Pocket **37/min** — both call sung notes kicks and
snares. Two designs measured:

| design | hums, false/min | synth bass, false/min | performer's real hits dropped |
|---|---|---|---|
| none | 76 | 64 | 0 |
| global 4-class MLP noise gate (AVP-unmatched + hums + synth), held-out source | 10–20 | 7–15 | **~50 %** (16/34, 18/43 on his fixtures) |
| same gate trained without his audio | — | — | still ~50 % |
| **per-user `noise` class** (k-NN, one hum take → tested on the other) | **11–13** | — | **0 / 77** |

A global "not a drum" class punishes whichever voice it hasn't seen (and
the performer whose hums it *has* seen); a per-user one from a single
~20 s voice clip rejects singing an order of magnitude better with nothing
lost.

**Speech (LibriSpeech dev-clean, 2026-10-25, 110 s / 10 files):** talking
into the open mic gives **mouthflow 212 false hits/min, Pocket 170/min**
(both mostly snare). With 2,892 speech onsets in the negative bank the
global gate rejects speech 230 → 40/min and hums → 2.5/min held out, but
silences **20/34 and 31/43** of the performer's real fixture hits — worse
than before. Conclusion is final: the global gate is not a product
path; the per-user `noise` clip must include *talking* as well as singing
and breathing. The 3,104-onset negative bank (`datasets/avp/cache/
negatives.all123.npz`) stays as a measurement set and as training
negatives for the no-enrollment global fallback only if a future variant
keeps the performer's hits (gate on it with `eval/negatives.py --fixtures`). Shipped on the Python side as the built-in `noise` sound (verdict
DROP; `enroll record noise --duration 20`, panel `enroll_noise`).

**Ask for Pocket:** the same clip could teach a 4th "noise" Teach voice
(k examples, vote → reject) — Pocket's forest noise class was trained on
synthetic breath controls and does not cover singing (37/min). The audio
to measure with is local: `datasets/bass/b01.hum.wav`, `b02.hum.wav`
(René's machine; `datasets/` is gitignored — copy, don't commit).
Synthetic breath swells did not generalise in either direction; real
breath/speech negatives are still wanted (LibriSpeech is the candidate).

## How a change travels

- Pocket changes features/metric/state → bump `feature_version` in
  `auto-model.json`, update `mouthflow/pocket.py` + tests in the same PR, rerun
  `eval.avp_pocket` and paste the table into the PR.
- mouthflow changes enrollment/profile export → rerun `eval.avp_pocket`
  (`teach3-medoid` row) and `tests/test_pocket.py`; no native change needed.
- Either side adds data → it goes under `datasets/` (gitignored) with a
  manifest; evals read manifests, never hard-coded paths.
