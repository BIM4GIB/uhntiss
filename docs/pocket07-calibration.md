# Pocket 07: the recordings still needed

The current candidate recovers some real hits after rejected candidates and
reduces humming errors. It does **not** pass the 50–100 ms roll stress test.
Human breath/speech rejection has not been measured. Keep the current player
until the new calibration and independent test support a better release.

Use the same mic, distance, input gain and **48,000 Hz** throughout. Wear
headphones. Record one sound per clip, without a metronome bleeding into the
mic. Do not use CAL 48k 0003: its snare/hat sections are mislabeled.

## Enrollment clips — may train a profile

Make separate `ENROLL 48K kick`, `ENROLL 48K snare`, `ENROLL 48K hat` and,
if wanted, `ENROLL 48K hat_open` clips.

For each sound, record about 30 seconds:

1. Eight comfortable isolated hits, roughly half a second apart.
2. Eight deliberately quiet hits of the same articulation, with the same gaps.
3. Eight accented isolated hits.
4. Three short rolls using **that sound only**: four attacks about 100 ms apart,
   then four about 75 ms apart, then four about 50 ms apart. Leave a second
   between rolls. Play the articulations you use in a groove, including your
   inward/outward versions if both belong to that voice.

The isolated hits are necessary for the 80 ms loop classifier. The short rolls
expose the preceding-sound context that isolated enrollment currently misses.
These instructions are an acquisition plan, not a claim the current profile
export already solves the roll failures.

Make a separate `ENROLL 48K noise` clip, about 25 seconds: five seconds of
normal inhaling/exhaling, five of the stronger breaths you take between phrases,
five of ordinary talking, five of humming/singing, and five of room silence.
Do not beatbox drum syllables in this clip. `noise` is rejection, never a MIDI voice.

Use `mouthflow enroll clip SOUND` for the selected Live clip, or
`mouthflow enroll file SOUND PATH` for a file. Keep these clips in the enrollment
manifest. Export against the new native tools with
`mouthflow enroll export-pocket --sr 48000`. Pocket 07 accepts feature-4 profiles;
Pocket 06.1 requires its earlier feature-3 export.

## Independent tests — never enrollment

After enrollment, record `TEST 48K fresh groove` for 20 seconds: improvise a new
mixed groove, vary dynamics, and leave a few natural breaths between phrases.
Record `TEST 48K fast rolls` for 15 seconds: mixed kick/snare/hat rolls and ghost
hits, with short pauses. Finally record `TEST 48K breath speech` for 20 seconds:
normal performance breaths and different spoken words, with no drums.

Keep all three files outside the enrollment manifest. Freeze their source hashes
and annotate the intended attacks before tuning on the results. Future development
can inspect their misses, but no profile, threshold, feature scaling, augmentation
or forest may fit these test takes. The existing `live_6_uptempo` reservation
remains permanent.
