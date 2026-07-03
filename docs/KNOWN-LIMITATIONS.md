# Known limitations

An honest, ranked list of the project's weaknesses, written for an external reviewer.
Ranked by how much each should affect your assessment, worst first. Everything below
is verified against the code as of this commit; file pointers are given so you can
check for yourself. Test suite status at time of writing: `uv run pytest` → 121 passed.
(A fuller adversarially-verified defect list lives in [`roadmap.md`](roadmap.md) §3;
the eval-integrity items there are now largely addressed — `run_eval` gates fail CI,
prints a train-contamination warning on the default corpus, and measures timing/
swing/velocity fidelity; `note_eval` counts wrong-pitch matches against precision;
`mimic/take.py` sanity-gates auto-labels. What remains true: the fixtures themselves
are still the contaminated N=2 set until fresh held-out takes are recorded.)

## 1. The LLM "taste" layer is unvalidated

The core thesis — a deterministic transcription pipeline whose musical judgment lives
in one Claude call ([`mouthflow/plan.py`](../mouthflow/plan.py), the only file that
touches the `anthropic` SDK) — is currently **unproven**. The prompt
([`mouthflow/prompts/plan.md`](../mouthflow/prompts/plan.md)) ships with few-shot
examples explicitly marked as placeholders ("These are placeholders. Replace each
with a real input/output pair"). The bass/lead/drone prompt sections have never been
tuned against real recordings. The A/B harness exists
([`eval/taste_review.py`](../eval/taste_review.py)) but no rated corpus does — it has
never produced a number. If you are auditing the "own the taste" claim: there is
machinery, not evidence.

## 2. The eval corpus is tiny

| What | Have | Spec target |
| --- | --- | --- |
| Tracked drum fixture trios (wav+mid+json) | 2 (`tests/fixtures/clips/01_boombap_mimic`, `02_bb100`) | N=20 ([`docs/spec.md`](spec.md) §Eval harness) |
| Drum classifier accuracy | LOO CV ≈ 0.81, held-out-take mean ≈ 0.73 ([`eval/classifier_cv.py`](../eval/classifier_cv.py)) on one user's small dataset | — |
| Pitched note eval | synthetic self-test only (clean sines, bass F1 1.0) ([`eval/note_eval.py`](../eval/note_eval.py)) | real hummed takes |

No `mimic/*.notegrid.json` reference grids exist yet, so the pitched voices (bass,
lead, drone) have **zero** accuracy numbers on real audio. The per-user k-NN timbre
classifier (`mouthflow/drum_model.json`, 10 features, heuristic fallback) will not
transfer to other voices/mics without recalibration. All quality numbers should be
read as "one performer, one laptop mic".

## 3. Drone automation is runtime-unverified

The drone voice emits a loudness-contour `AutomationEnvelope` applied via
`set_clip_envelope` ([`mouthflow/execute.py`](../mouthflow/execute.py) ~line 291).
The bridge-side handler ([`bridge/set_clip_envelope.py`](../bridge/set_clip_envelope.py))
compiles and has been code-reviewed, but has **never been exercised inside a running
Live set** — its Live Object Model behaviour is unverified. Mitigation: failure is
graceful (logged as "automation skipped"; the drone still plays as a held note/chord).
`get_selected_clip`, by contrast, has been verified live.

## 4. The Ableton bridge fork is a manual splice

Beyond stock [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)
(TCP :9877), two commands ([`bridge/get_selected_clip.py`](../bridge/get_selected_clip.py),
[`bridge/set_clip_envelope.py`](../bridge/set_clip_envelope.py)) must be **hand-pasted
into the installed Remote Script** per [`bridge/README.md`](../bridge/README.md) — no
installer, no patch script. On a stock install, `transcribe-clip` fails (with a clear
error) and drone automation is skipped. Anyone reproducing this setup will hit it
immediately; it works on the dev machine because the splice was done by hand there.

## 5. Kit discovery is polluted

`list_drum_instruments` returns one-shot samples mixed in with real `.adg` drum racks,
so sampling a subset for the planner can drop entire kit families (see
`list-kits` in [`mouthflow/cli.py`](../mouthflow/cli.py)). Known and reproduced;
no filter implemented yet.

## 6. The intent router is an unmeasured heuristic

`--device auto` routing ([`mouthflow/classify.py`](../mouthflow/classify.py)) is
threshold logic over pyin voiced-fraction and pitch stability (voiced < 0.4 → drums;
pitch std < 1.0 → drone; register split at MIDI 52). Known failure documented in the
module docstring: a hummed *chord* drone (moving pitch) misroutes to bass/lead.
There is no labelled routing set, so its accuracy is simply unknown. Mitigation:
explicit `--device` always overrides.

## 7. pyin window blur on fast bass lines

The bass device uses `frame_length=4096` (~93 ms at 44.1 kHz) for reliable low-f0
estimation ([`mouthflow/devices/bass/device.py`](../mouthflow/devices/bass/device.py)),
plus segmentation hysteresis `min_stable_s=0.08`
([`mouthflow/devices/pitched.py`](../mouthflow/devices/pitched.py)). Notes faster than
roughly 1/8s at bass register smear together. This is an inherent time/frequency
trade-off, not a bug, but it caps how busy a bassline you can hum in.

## 8. `refine.py` bakes in judgment calls

[`mouthflow/refine.py`](../mouthflow/refine.py): bar fitting supports 1/2/4/8/16 bars
(auto rounds **up**, then to multiples of 8), assumes 4/4 throughout, and clamps
overhang. Key detection is Krumhansl-Schmuckler with a confidence-weighted histogram
that **falls back to C major** when the pitch signal is thin — a wrong key silently
snaps notes. Mitigations: `--key`/`--scale` override; `correct_notes` only snaps notes
with confidence < 0.75 (confident notes are trusted — forcing one scale corrupted a
real chromatic bassline); `--no-correct` and `--bars off` disable both; sustained
(drone) clips are never scale-snapped at all — the held pitch is kept verbatim, and
`--key`/`--scale` are ignored with a logged notice.

## 9. Fallback instrument URIs are guesses

Each `DeviceSpec` carries `fallback_instruments` (e.g. `query:Sounds#Bass:Sub`,
[`mouthflow/devices/bass/device.py`](../mouthflow/devices/bass/device.py)) used when
browser scanning fails. These URI strings were never validated against a real Live
browser and may not resolve; they are documented as offline/dry-run material
([`mouthflow/devices/base.py`](../mouthflow/devices/base.py)).

## 10. Ops gaps

- No linter or type-checker is configured (nothing in `pyproject.toml`). CI exists
  ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) but runs only the
  75-test suite — no lint/type gate.
- TCP :9877 is a single socket; another MCP client (e.g. Claude Desktop with the same
  bridge) contends with the CLI for it.
- Anything involving a real Live set (M4L panels in [`m4l/`](../m4l/), bridge
  commands, browser loading) is only manually verifiable; tests stub the socket.

## 11. Naming debt

The package is `mouthflow`; the repo codename is `uhntiss`. Neither is the final name,
and the rename surface (package dir, M4L device titles, `~/Music/Ableton/User
Library/Devices/Mouthflow*.amxd` + `mouthflow*.js`, prompt text, docs) is undocumented. Cosmetic, but a
rename late in the day will touch installed artefacts on users' machines.

---

Three confidence gates worth knowing about while reviewing (all deliberate, all
tunable): pitched notes with mean voiced-prob < 0.2 are dropped as blips
(`pitched.py`); scale-snap skips notes with confidence ≥ 0.75 (`refine.py`); drum
tempo quantisation only engages at tempo confidence ≥ 0.5
([`mouthflow/devices/drum/tempo.py`](../mouthflow/devices/drum/tempo.py)).
