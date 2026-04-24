# Tests

Unit tests run against the fixture corpus in `fixtures/clips/` and against
mocked sockets / LLM clients.

## Manual integration test

`execute.py` is the only module that touches a live Ableton instance. To
verify it:

1. Install [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)
   as a Live Remote Script and enable it in Live's MIDI preferences.
2. Open an empty Ableton Live set.
3. Run `python -m mouthflow.cli run tests/fixtures/clips/01_basic_4to4.wav`.
4. Expect: a new MIDI track with a drum rack loaded and a clip playing the
   transcribed pattern. Tempo updated to match the clip.

Document deviations here as they arise.
