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

## Interfaces

**1. Detection stream** — `native/pocket/build/pocket_analyze <sr> [gate] [policy]`
reads mono float32 PCM on stdin and prints one CSV row per detection:
`beat, drum, velocity, time_s, f1..f17`. `drum` 0/1/2 = kick/snare/hat,
3 = rejected. Python: `mouthflow.pocket.analyze` / `accepted`.

**2. Neighbour metric** — `pocket::model::scale` (17 doubles in
`attack_model.hpp`, the *first* array; `recovery` and `highrate` have their
own). `Profile::classify` = 3 nearest examples by Σ((a−b)/scale)², weight
1/(1+d), argmax. Python replica: `mouthflow.pocket.vote` (ties follow the
C++ insertion order; `tests/test_pocket.py` pins it).

**3. Profile payload** — the v4 state's profile block alone:
`3 × (count, 3 × 17 features)` = **156 doubles** (`pocket::profileValues`).
- Native: `decodeProfile(values, count, profile)` in `state.hpp` (same
  validation as `decodeState`'s v4 branch; loop untouched).
- Max: the external accepts **`profile <156 numbers>`** (`A_GIMME`); it
  queues `Action::profile → engine.setProfile` and updates `savedProfile`, so
  the pattr `loop_state` persists it with the Set exactly like a taught one.
- File: `~/.mouthflow/pocket_profile.json` (canonical) **and** a copy named
  `uhntiss_pocket_profile.json` in every `~/Documents/Max */Library/` — Max
  resolves a bare filename through its search path, so the device needs no
  home-directory logic. Shape: `{"version": 4, "values": [156], "sample_rate",
  "pick", "source": {...}}`.
- Device: the **PROFILE** button (top row, left of INPUT VOICE) →
  `js pocket-profile.js` reads the file and sends `profile …` to the engine.
  *Not yet clicked in a real Live session — the external compiles and the
  patch generates; first click is on the test plan.*

**4. Enrollment → profile** — `uv run mouthflow enroll export-pocket [--sr 48000]`
runs Pocket's own detector over every enrolled clip (one class per clip,
see `mouthflow/enroll.py`), labels detections by the clip, and keeps the
**3 medoids per voice** in Pocket's metric. Open-hat clips count as hats
(Pocket maps open to closed).

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

## How a change travels

- Pocket changes features/metric/state → bump `feature_version` in
  `auto-model.json`, update `mouthflow/pocket.py` + tests in the same PR, rerun
  `eval.avp_pocket` and paste the table into the PR.
- mouthflow changes enrollment/profile export → rerun `eval.avp_pocket`
  (`teach3-medoid` row) and `tests/test_pocket.py`; no native change needed.
- Either side adds data → it goes under `datasets/` (gitignored) with a
  manifest; evals read manifests, never hard-coded paths.
