# Tests

Unit tests run against the fixture corpus in `fixtures/clips/` and against
mocked sockets / LLM clients / audio streams. All offline:
`uv run pytest` needs no Live, mic, or API key.

## Manual integration test

`execute.py` is the only module that touches a live Ableton instance. To
verify it:

1. Install [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)
   as a Live Remote Script and enable it in Live's MIDI preferences.
2. Open an empty Ableton Live set.
3. Run `uv run mouthflow run tests/fixtures/clips/01_boombap_mimic.wav`.
4. Expect: a new MIDI track with a drum rack loaded and a clip playing the
   transcribed pattern. Tempo updated to match the clip.

Per-voice variants: repeat with `--device bass|lead|drone` on a hummed clip —
expect a pitched instrument + MIDI with real note durations (drone: a held
note/chord). With the bridge fork installed (see `bridge/README.md`), also
verify `uv run mouthflow transcribe-clip --device bass` on a selected audio
clip, and — still unverified at runtime — drone's macro-automation write.

Document deviations here as they arise.
