# Mouthflow — audit findings & the "make it magic" roadmap

Written 2026-07-03, from a deep multi-agent audit of this repo (health check on
this machine, six subsystem deep-reads, market + state-of-the-art research,
four audit dimensions with per-finding adversarial verification, three
competing roadmap visions scored by a judge). Companion to
[`handover.md`](handover.md) (current state) and
[`KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md) (self-reported gaps). This doc
is the *plan*: what we found, what "magic" concretely means for this product,
and the sequenced work.

---

## 1. Where the product actually stands

Ground truth measured on this machine (2026-07-03, branch `feat/drum-quality`):

- `uv run pytest -q` → **75 passed** (4 third-party DeprecationWarnings;
  `audioread` imports `aifc`/`audioop`/`sunau`, removed in Python 3.13 —
  fine on the pinned 3.11, a blocker for a future bump).
- `uv run python -m eval.run_eval` → onset F1 0.87, class acc **0.95**, tempo
  2/2. But see finding E1: this number is **train-set contaminated** and the
  gates **cannot fail**.
- `mouthflow doctor` → Live + Remote Script reachable on :9877 (project tempo
  156.61), 1000 kits discovered. API-key check fails unless `.env` is sourced
  (doctor doesn't read `.env` itself — the M4L glue does; see R7).

The architecture is genuinely good: the DeviceSpec registry, the confidence-
gating discipline (pitch snap ≥0.75 keep, blip drop <0.2, tempo quantise gate
≥0.5), the byte-identical drum refactor guard, and the honest docs culture are
all assets. The problems are concentrated in four places: **trust** (session-
hostile behaviours, lost takes), **honesty of the eval**, **latency dead air**,
and a **taste layer that is starving** (the one LLM call receives four numbers
and a histogram).

## 2. What "better than autotune and Shazam together" means

From the market research (Dubler 2, imitone, Live 12 convert, Melodyne,
Samplab, Basic Pitch/NeuralNote, SoundID VoiceAI, Suno covers, Google
hum-to-search):

**The whitespace is exactly this product.** Nobody ships
*hum/beatbox → clean quantised MIDI + an automatically well-chosen instrument,
inside the DAW*. Dubler makes you pick the sound first and edit spaghetti MIDI
after; Live 12's converter is transient-based, 3-piece-drum-limited, and
instrument-dumb; Melodyne/Basic Pitch stop at offline MIDI; Suno gives an
uneditable song outside your session. The two halves of the ambition map to:

- **Better than autotune** = *decode intent, don't transcribe mistakes*.
  Google hum-to-search proves off-key humming carries recoverable melodic
  intent. Mouthflow's confidence-gated preservation (snap only what the
  performer was unsure of) is the right philosophy — extend it from pitch to
  **timing** (swing/groove) and **dynamics** (ghosts/accents).
- **Better than Shazam** = *it always answers, fast, visibly, with taste*.
  The planner must actually hear the performance (swing, ghosts, key, session
  context) and answer in seconds with a confident, evocative rationale.

The market's six-point magic bar (nobody clears all six today): works on a
laptop mic with no training session; tolerates off-key/loose input; plays back
as music within seconds; **no piano-roll surgery needed**; drums beyond 3
pieces without misfires; local, no account, no subscription. Clearing all six
*is* the product. The core quality metric to adopt: **no-edit-needed rate**.

Pricing/positioning anchors: $49–99 one-time, local-first (the market
collapsed there: Dubler $249→$99; subscriptions are actively resented).
Ableton's own trajectory (12.3 stem separation) makes the *instrument-choice*
layer — which Ableton has never attempted — the durable moat.

## 3. Confirmed defects (adversarially verified)

27 findings survived adversarial verification (1 refuted). Ranked; fix
sketches in §4's workstreams.

### Correctness (DSP/math)
| # | sev | where | what |
|---|-----|-------|------|
| C1 | **critical** | `refine.py:118` + `devices/drone/transcriber.py` | Drone notes are **silently transposed**: drone `NoteEvent`s carry `confidence=None`, so the ≥0.75 keep-gate is inert, and a single-pitch drone (<2 distinct pitch classes) always trips the C-major fallback — all 5 out-of-scale pitch classes snap DOWN a semitone (C#→C, F#→F …). Hum F#, get F. |
| C2 | major | `execute.py:263` | `_midi_to_notes` keeps one pending note per pitch; overlapping same-pitch notes (the drone's *normal* case — every region sustains to clip end) drop the earlier note. |
| C3 | major | `refine.py:192` | `fit_to_bars` overrides the drone's own bar count without extending held-note durations → silent bars in the looped drone; the loudness envelope also stops short of the loop end. |
| C4 | major | `devices/drum/tempo.py:116` | Octave disambiguation structurally **doubles tempos below ~76 BPM**: the flat +0.10 out-of-band prior outweighs real grid evidence (~0.05 separation), and the IOI-lattice k-set is missing k=4. Doubled tempo/bars are emitted even when the confidence gate suppresses quantise. |
| C5 | major | `devices/drum/tempo.py:141` | `--no-bar-align` phase-aware snap can emit **negative note times** (negative phase + first-cell onset) → mido `ValueError`, whole take crashes. |
| C6 | minor | `signal.py:78` | `n_fft` computes the *next* power of two rather than capping at frame length — the librosa warning it claims to avoid still fires on tail windows. |

### Robustness (a real user will hit these)
| # | sev | where | what |
|---|-----|-------|------|
| R1 | **critical** | `execute.py:78` | A socket read timeout escapes as raw `TimeoutError` and **permanently desynchronises** the :9877 connection — subsequent commands consume stale responses. Framing also splits multi-byte UTF-8 (only `JSONDecodeError` is caught, not `UnicodeDecodeError`); no connect timeout. |
| R2 | major | `cli.py:151` | Silence/breath/dead-mic takes produce 0 notes but still burn an LLM call, create a junk track + empty clip, and stomp the project tempo. No zero-notes guard. |
| R3 | major | `cli.py:216` | `record`/`record-stream` capture **before** opening the socket, with no error handling — Ableton not running = raw traceback and the performance is lost (temp WAV never surfaced). |
| R4 | major | `m4l/mouthflow.js:54` | Glue hard-codes `~/.local/bin/uv`; Homebrew installs → every button ENOENT, no panel control to fix it. |
| R5 | major | `plan.py:159` | Missing API key / network failure surfaces *after* the take as an unhandled traceback. |
| R6 | major | `execute.py:284` | `apply_plan` unconditionally `set_tempo(plan.tempo)` — **every take overwrites the project tempo**. Also: stepwise mutation with no rollback → failed instrument load leaves tempo changed + junk track. |
| R7 | minor | `cli.py:382` | `doctor` reads only `os.environ` while the glue parses `.env` — doctor fails where the device succeeds. |
| R8 | minor | `capture.py:130` | `from_file` writes `.normalised.wav` next to the source — litters Live project folders; crashes on read-only media. |
| R9 | minor | `m4l/mouthflow.js:177` | "beatbox now!" fires at spawn; capture opens ~0.5 s later (uv + import chain) — **the first hit of every M4L take is clipped**. |

### Latency (measured on this machine)
| # | where | dead air |
|---|-------|----------|
| L1 | `cli.py:66` | Browser walk re-runs every take, serialized after transcription: **7.0 s** (Drums) / 3.1 s (Sounds). Never cached. |
| L2 | `plan.py:168` | Plan call **5.6–6.3 s**; ~7.1 K input tokens re-processed per take because the (stable) instrument list sits *after* the cache breakpoint and is pretty-printed. The `cache_control` block as-written buys almost nothing. |
| L3 | `classify.py:30` | `--device auto` runs full-clip pyin **twice** (router pass, 2.26 s, thrown away; device re-loads + re-analyses). |
| L4 | `execute.py:282` | `apply_plan` = 7–8 strictly serial roundtrips at 0.4–0.64 s each; project tempo fetched only after the take ends. |
| L5 | `cli.py` | Every M4L press pays ~0.44 s uv+import cold start, even for pure socket commands (`list-kits`, `input-devices`). |

Warm-path total today: stop-recording → clip ≈ **15–20 s** of mostly avoidable
dead air. Target: **<10 s warm, <5 s stretch** — without a daemon if caching
gets us there (only promote a `serve.py` engine if measured numbers demand it).

### Eval integrity (the numbers are not what they claim)
| # | sev | where | what |
|---|-----|-------|------|
| E1 | **critical** | `eval/run_eval.py:148` | Both fixture WAVs are **byte-identical to the k-NN training takes**. The 0.95 headline class accuracy is train-on-test. The honest number is `classifier_cv.py`'s held-out ≈0.73. |
| E2 | major | `eval/run_eval.py:214` | The "gates" cannot fail — `run_eval` always exits 0; CI runs pytest only. The documented regression oracle is unenforced. |
| E3 | major | `mimic/take.py:174` | Fixture ground truth is generated by aligning the grid with the pipeline's *own* onset detector (circularity); the promised label sanity check is dead code. |
| E4 | major | `eval/note_eval.py:72` | Wrong-pitch (non-octave) time-matched notes count as neither TP nor FP — precision silently inflated. |
| E5 | major | `eval/run_eval.py:71` | Velocity has no oracle anywhere: GT velocity is constant 90; `velocity_from_rms` has zero tests. |
| E6 | minor | `eval/note_eval.py:41` | Offset search unconstrained (±0.5 s, count-only) — the slot-aliasing failure the drum harness guards against. |
| E7 | minor | `eval/run_eval.py:184` | Tempo gate floors the threshold: at N=2 the printed "≥80%" passes at 50%. |

Also stale: `handover.md` quotes "~0.97" class accuracy; today's (contaminated)
measurement is 0.95, and the honest held-out figure is ≈0.73.

### Taste layer (the product thesis, starving)
- `plan.py:92` sends the model **four numbers and a histogram**. The prompt
  *claims* it conveys "density, swing"; it doesn't. No velocity shape, no
  pattern skeleton, no key, no session context. The detected key is computed
  in `refine` (`cli.py:142-148`) and **discarded** before planning.
- `prompts/plan.md` few-shots are placeholders (tracked as KNOWN-LIMITATIONS
  #1). Model pinned to `claude-sonnet-4-6` at default temperature; hallucinated
  URIs silently fall back to `available[0]` (alphabetically first kit) rather
  than a fuzzy name match.
- Intent signals that already exist in the codebase and die before Live:
  swing lean (`eval/timing_probe.py:60-77` — measured, then thrown away), grid
  phase (`devices/drum/tempo.py:123-141`, discarded under `bar_align`),
  continuous f0 + per-frame RMS (`devices/pitched.py:55-64`, flattened),
  detected key (discarded). **The intent-preservation product already half-
  exists internally; it's just never plumbed through.**

## 4. The roadmap

Three visions were drafted and judged (immediacy / transcription quality /
zero-friction trust). Winner: **deep-quality — "it hears what you MEANT"** —
stealing zero-friction's trust backbone first, and deferring the realtime
monitor bet until the engine + honest eval exist. Sequenced so every stage
ships a user-feelable improvement.

### NOW (weeks 1–2) — "it never damages my session or loses my take, and it answers me"

> **Status: shipped 2026-07-03** on `feat/now-trust-batch` (items 1–4 below,
> plus fixes from an adversarial review pass: a public `AbletonTransportError`
> so "couldn't ask" is never classified as an answer, automation envelopes
> shifted/clamped with bar-fit, `--set-tempo` implies detection, pre-capture
> device validation, and the pitched panels now show the live input level).
> `python m4l/generate.py --install` remains the manual sync step.
> See `handover.md` for the behaviour changes.

1. **One clock** ⭐ *the single highest-leverage change (~30 lines)*:
   `record` fetches the project tempo before capture (the exact pattern
   already in `record-stream` at `cli.py:276-284` and `transcribe-clip` at
   `cli.py:459-467`); `apply_plan`'s `set_tempo` becomes opt-in
   (`--set-tempo`, panel toggle). Kills R6, gives pitched voices a trusted
   grid for free (`pitched.py:53` stops calling `detect_tempo` on rhythm-less
   hums), and every timing improvement below assumes this shared grid.
   Include: allow 1/2-bar loops in `fit_to_bars` (`refine.py:51` — today a
   1-bar riff gets 3 bars of dead air), and trim the lead-in.
2. **Never lose a take**: connect + ping *before* capture (reorder
   `cli.py:216`); persist every capture to `~/.mouthflow/takes/<ts>.wav` +
   `last_take.json`; add `mouthflow retry-last`; zero-notes guard before
   `make_plan` (fixes R2, R5 becomes a pre-take check); socket hardening in
   `execute.py` (connect timeout, reconnect+single-retry on read timeout,
   decode-tolerant framing, widen the automation `except` so `fire_clip`
   always runs) — fixes R1.
3. **Honest feedback**: count-in emitted CLI-side at actual stream-open
   (fixes R9/the clipped first hit); "HEARD 14 hits @ 84 BPM" line right after
   transcription; wire the already-emitted `rationale`/`error` outlets into
   the panels; `LEVEL` meter lines from the capture callback; doctor reads
   `.env` (R7) and grows a `--bridge` probe.
4. **Correctness batch** (small, testable): drone confidence + skip
   scale-snap for `SUSTAINED` (C1); `_midi_to_notes` per-pitch FIFO (C2);
   drone bar-fit extends durations (C3); clamp negative snap times (C5);
   fix E7's floor. Each with a regression test.

### NEXT (weeks 3–6) — "it comes back sounding like ME, fast, and I can iterate"

5. **Honest eval FIRST** (gate for all feel work): ~10 genuinely held-out drum
   takes via `mimic/take.py` with the label sanity check revived (E3); first
   real bass/lead `notegrid` references; fix note_eval's wrong-pitch and
   offset-window holes (E4, E6); promote `timing_probe`'s swing/displacement
   math into `run_eval` as **gated, fail-capable** metrics (E2) — velocity
   rank-correlation included (E5); add `run_eval` to CI. Re-baseline and
   correct the stale ~0.97 in the docs. *No feel tuning before this lands —
   anything else is fitting noise to md5-identical fixtures.*
6. **Groove + dynamics** (the "better than autotune" core): swing-aware,
   strength-blended quantise (default ~0.6) reusing `_grid_phase`/
   `_quantise_grid`; fix `bar_align` discarding phase; fix C4's octave prior
   (distance-scaled penalty + k=4 lattice, plus a 70 BPM fixture); per-take
   percentile velocity normalisation replacing the absolute-dB map
   (`signal.py:112`) so ghosts stay ghosts and accents hit. Re-baseline the
   drum oracle deliberately in the same PR.
7. **Speed without a daemon**: cache the browser walk per category with TTL,
   warm it in a thread *during* capture (L1); fix the prompt cache so the
   instrument list is inside the cached prefix, compact the JSON, temperature
   ~0.2, consider a model bump (L2); single pyin pass for `--device auto`
   (L3); per-stage timing lines. Target warm stop-to-clip **<10 s**; only if
   this fails does a persistent `mouthflow serve` engine get promoted.
8. **Installer + iterate-in-place** (they both deepen fork dependence, ship
   together): vendor the complete forked Remote Script in-repo + `mouthflow
   setup` (copy, backup, verify, `get_bridge_version` handshake) replacing the
   hand-splice; then retake-on-same-track (replace clip, keep instrument, no
   second LLM call) and re-refine-from-cached-take ("make it 8 bars, F# minor"
   without re-performing).

### LATER (weeks 7+) — "it's an instrument with taste"

9. **The planner finally hears the performance**: swing %, ghost/accent
   ratio, per-bar density, bar-1 pattern skeleton, detected key (stop
   discarding it), note-length character, session track names via
   `get_session_info`. Real few-shots from the rated corpus
   (`eval/taste_review.py` finally produces a number). Delight layer:
   evocative clip names via stock `set_clip_name`, key-aware follow-up
   suggestion ("hum a bassline in F minor"), fuzzy fallback instead of
   `available[0]`. Filter kit-discovery pollution via the existing
   `instrument_filter` hook (KNOWN-LIMITATIONS #5).
10. **Neural ears, behind existing seams, measured before default**:
    `PitchTracker` strategy seam — **SwiftF0** (MIT, ONNX, ~40× CPU-realtime,
    +12 pts over CREPE at 10 dB SNR = laptop-mic conditions) as the pyin
    replacement candidate; embedding + per-user prototype enrollment drum
    classifier (AVP-LVT recipe, ~30 s enrollment take) replacing hand-crafted
    k-NN — the honest-eval notegrids/held-out takes from #5 are the gate for
    flipping any default. Licensing: SwiftF0/FCPE/torchcrepe MIT, basic-pitch
    Apache-2.0, PESTO LGPL-3.0 (unmodified dep OK), avoid madmom/BeatNet for
    commercial paths.
11. **Expression channel**: glides → pitch-bend, swells → envelopes via
    `set_clip_envelope` (after runtime-verifying it in a real set —
    KNOWN-LIMITATIONS #3); re-articulation splits from the already-computed
    RMS dips; whole-line octave continuity.
12. **The monitor bet** (play-it-live's realtime loop): persistent engine +
    streaming onsets/PESTO pitch → M4L noteout, drums first, opt-in,
    ~60–120 ms mouth-to-sound with the offline pass as the "polish truth".
    Attempted only on top of the honest eval (per-hit confidence must exist)
    and only if #7's numbers prove the daemon is needed anyway.

## 5. North-star metrics

- **No-edit-needed rate** (the market's #1 complaint is "the MIDI needs
  cleanup") — measured per take in `taste_review`-style sessions.
- Warm stop-to-clip latency (<10 s now, <5 s stretch).
- Held-out drum class accuracy (honest baseline ≈0.73 today) and pitched
  note F1 on real hummed notegrids (none exist yet).
- Swing-preservation error + velocity rank correlation (new in run_eval).
- Fresh-Mac-to-first-clip time (<5 min via `mouthflow setup`).
