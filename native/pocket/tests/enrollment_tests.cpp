#include "pocket/state.hpp"
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <new>
#include <vector>
static bool inAudio = false;
static int audioAllocations = 0;
void *operator new(std::size_t n) {
  if (inAudio)
    ++audioAllocations;
  if (auto *p = std::malloc(n ? n : 1))
    return p;
  throw std::bad_alloc();
}
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
using namespace pocket;
static int checks = 0;
void check(bool ok, const char *message) {
  ++checks;
  if (!ok) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}
static double input(double t, double onset) {
  const double d = t - onset;
  return d >= 0. && d < .07 ? .6 * std::sin(d * 2. * 3.141592653589793 * 110.) *
                                  std::exp(-d * 40.)
                            : 0.;
}
int main() {
  auto profile = std::make_unique<Profile>();
  auto &p = *profile;
  p.voices = maxVoices;
  p.sampleRate = 48000.;
  p.scale.fill(3.);
  for (int d = 0; d < maxVoices; ++d) {
    std::snprintf(p.names[d].data(), 25, "voice_%d", d);
    p.notes[d] = 35 + d;
    p.count[d] = maxExamples;
    p.lateCount[d] = maxExamples;
    for (int k = 0; k < maxExamples; ++k) {
      p.examples[d][k].fill(d);
      p.late[d][k].fill(std::int8_t(d));
    }
  }
  p.rejects = maxRejects;
  p.lateRejects = maxRejects;
  p.negativeSlices.fill(7);
  StateValues payload{};
  Loop loop;
  loop.append({.123456789, .37f, Drum::noise, 15, 50});
  for (size_t i = 1; i < maxEvents; ++i)
    loop.append({.2 + double(i) * .01, .37f, Drum::noise, 15, 50});
  const auto length = encodeState(loop, p, payload);
  auto decoded = std::make_unique<Profile>();
  Loop restored;
  check(length < 32767 &&
            decodeState(payload.data(), length, restored, *decoded),
        "maximum v5 state fits a Max list and decodes");
  check(decoded->examples == p.examples && decoded->late == p.late &&
            decoded->names == p.names,
        "all voices and all examples round-trip");
  check(restored.events[0].note == 50 && restored.events[0].beat == .123456789,
        "custom MIDI note and pocket survive save");
  const auto valid = payload;
  payload[3 + 5 * maxEvents + 246] = 128;
  check(!decodeState(payload.data(), length, restored, *decoded) &&
            decoded->notes[15] == 50,
        "bad MIDI note rejects transactionally");
  payload = valid;
  payload[3 + 5 * maxEvents + 246 + 30 + maxExamples * 17] = 4294967296.;
  check(!decodeState(payload.data(), length, restored, *decoded),
        "packed features reject overflow");
  Detection custom{Drum::noise, .4f, 0.};
  custom.features.fill(15.);
  check(p.classifyVoice(custom) == 15,
        "factory rejection cannot veto an enrolled voice");
  p.rejects = 1;
  p.negative[0] = custom.features;
  check(p.classifyVoice(custom) == 15,
        "one ambiguous negative cannot erase a real hit");
  custom.features.fill(14.5);
  p.rejects = 4;
  for (int i = 0; i < 4; ++i)
    p.negative[i] = custom.features;
  check(p.classifyVoice(custom) == -1,
        "four nearby personal negatives reject without a factory veto");
  p.rejects = 0;
  p.radius.fill(.01);
  custom.features.fill(30.);
  check(p.classifyVoice(custom) == -1,
        "outside enrollment support is rejected");

  {
    Profile bank;
    bank.voices = 2;
    bank.sampleRate = 48000.;
    bank.scale.fill(1.);
    bank.rejects = bank.lateRejects = 4;
    bank.negativeSlices.fill(1); // Complete first slices, truncated tails.
    for (int d = 0; d < 2; ++d) {
      bank.count[d] = bank.lateCount[d] = 3;
      for (int k = 0; k < 3; ++k) {
        bank.examples[d][k].fill(d);
        bank.late[d][k].fill(std::int8_t(16 + d * 16));
      }
    }
    for (int i = 0; i < 4; ++i) {
      bank.negativeLate[i].fill(-64);
      bank.negative[i].fill(-10.);
    }
    bank.prepareRejector();
    check(bank.noiseReady,
          "partial negative banks derive a bounded import cache");
    Detection ambiguous{Drum::noise, .12f, .123};
    Slices first;
    first.samples = {480, 0, 0};
    first.values.fill(-4.);
    inAudio = true;
    const auto rejected = bank.classifyVoice(ambiguous, &first);
    ambiguous.drum = Drum::hat;
    const auto ghost = bank.classifyVoice(ambiguous, &first);
    inAudio = false;
    check(rejected == -1 && ghost >= 0,
          "ambiguity rejection preserves a factory-positive ghost");
    check(bank.classifyLate(first, ghost) == ghost,
          "partial negatives cannot enter the legacy delayed veto");
    StateValues encoded{};
    const auto count = encodeProfile(bank, encoded.data());
    Profile imported;
    check(decodeProfile(encoded.data(), count, imported) && imported.noiseReady,
          "state restore rebuilds the cache outside DSP");
    check(imported.noiseScores == bank.noiseScores,
          "derived noise decisions survive exact profile round-trip");
    encoded[1] = 3.;
    check(decodeProfile(encoded.data(), count, imported) &&
              !imported.noiseReady && imported.metricVersion == 3,
          "feature-3 profiles preserve their legacy classifier");
    const auto preserved = imported.metricVersion;
    encoded[1] = 5.;
    check(!decodeProfile(encoded.data(), count, imported) &&
              imported.metricVersion == preserved,
          "unknown metrics fail transactionally");
  }

  auto extractor = std::make_unique<SliceExtractor>();
  extractor->prepare(48000.);
  Detector detector;
  detector.prepare(48000.);
  Detection detected{};
  bool got = false;
  for (int i = 0; i < 24000; ++i) {
    const auto x = input(i / 48000., .2);
    extractor->push(x);
    Detection d{};
    if (detector.process(x, i / 48000., .010, d, Detector::Policy::enrolled) &&
        !got) {
      detected = d;
      got = true;
    }
  }
  check(got, "fixture has a detected onset");
  const auto start = std::int64_t(std::round(detected.beat * 48000.));
  const auto slices = extractor->extract(start, start + 3840);
  const auto clipped = extractor->extract(start, start + 2400);
  check(clipped.samples == std::array<int, 3>{480, 1440, 480},
        "50 ms roll truncates tail at its onset");
  const auto before = clipped.values;
  for (int i = 0; i < 1000; ++i)
    extractor->push(3.);
  check(extractor->extract(start, start + 2400).values == before,
        "later audio cannot alter a truncated descriptor");
  p = Profile{};
  p.voices = 2;
  p.sampleRate = 48000.;
  p.scale.fill(3.);
  p.notes[0] = 36;
  p.notes[1] = 37;
  for (int d = 0; d < 2; ++d) {
    p.count[d] = 3;
    p.lateCount[d] = 3;
    for (int k = 0; k < 3; ++k) {
      p.examples[d][k] = detected.features;
      if (d)
        for (auto &f : p.examples[d][k])
          f += 10.;
      for (int j = 0; j < 120; ++j)
        p.late[d][k][j] =
            d ? std::int8_t(std::clamp(
                    int(std::round(slices.values[j] / 3. * 16)), -128, 127))
              : 127;
    }
  }
  check(p.classifyLate(slices, 0) == 1,
        "later body/tail can revise the provisional voice");
  Slices shortRoll = slices;
  shortRoll.samples[1] = 100;
  shortRoll.samples[2] = 0;
  check(p.classifyLate(shortRoll, 0) == 0,
        "roll before 40 ms keeps early decision instead of comparing unequal "
        "windows");
  auto engine = std::make_unique<Engine>();
  engine->prepare(48000.);
  engine->setProfile(p);
  engine->arm();
  int audible = -1, stored = -1;
  double l, r;
  for (int i = 0; i < 24000; ++i) {
    inAudio = true;
    engine->process(input(i / 48000., .2), {i / 24000., true, true}, l, r);
    inAudio = false;
    if (audible < 0 && engine->hitSerial())
      audible = i;
    if (stored < 0 && engine->capture().count)
      stored = i;
  }
  check(audible == start + 479,
        "audible event remains at the ten-millisecond sample");
  check(stored == start + 3839, "loop redecision arrives at 80 ms exactly");
  check(engine->capture().count == 1 &&
            engine->capture().events[0].voice == 1 &&
            engine->capture().events[0].note == 37,
        "loop stores revised custom voice");
  check(engine->capture().events[0].beat == detected.beat * 2. &&
            engine->capture().events[0].velocity == detected.velocity,
        "commit preserves exact onset and velocity");
  check(engine->synthTriggerCount() == 1,
        "late redecision never retriggers the audible kit");
  engine->undo();
  for (int i = 24000; i < 25000; ++i)
    engine->process(0., {i / 24000., true, true}, l, r);
  check(engine->capture().count == 0 && engine->loop().count == 0,
        "cancelled capture stays cancelled");
  check(audioAllocations == 0, "capture, feature extraction and classification "
                               "allocate nothing in process");
  // Each of these control boundaries must discard an unfinished 80 ms decision.
  for (int action = 0; action < 4; ++action) {
    auto e = std::make_unique<Engine>();
    e->prepare(48000.);
    e->setProfile(p);
    e->arm();
    for (int i = 0; i < 11000; ++i)
      e->process(input(i / 48000., .2), {i / 24000., true, true}, l, r);
    check(e->hitSerial() == 1 && e->capture().count == 0,
          "boundary fixture has a provisional, uncommitted hit");
    if (action == 0)
      e->clear();
    if (action == 1)
      e->undo();
    if (action == 2)
      e->restore(Loop{});
    if (action == 3)
      e->prepare(96000.);
    for (int i = 11000; i < 16000; ++i)
      e->process(0., {i / 24000., true, true}, l, r);
    check(e->capture().count == 0 && e->loop().count == 0,
          "clear/undo/restore/rate change cannot resurrect a pending hit");
  }
  // Three distinct attacks 50 ms apart: compare to the exact detector timeline.
  auto roll = std::make_unique<Engine>();
  roll->prepare(48000.);
  roll->setProfile(p);
  roll->arm();
  Detector rd;
  rd.prepare(48000.);
  std::vector<Detection> references;
  for (int i = 0; i < 24000; ++i) {
    const double t = i / 48000.;
    double x = 0.;
    for (int n = 0; n < 3; ++n)
      if (t >= .2 + n * .05 && t < .22 + n * .05)
        x += input(t, .2 + n * .05);
    Detection hit{};
    if (rd.process(x, i / 24000., .010, hit, Detector::Policy::enrolled))
      references.push_back(hit);
    roll->process(x, {i / 24000., true, true}, l, r);
  }
  check(references.size() == 3 && roll->capture().count == 3,
        "50 ms roll retains all three detected hits");
  for (size_t i = 0; i < references.size(); ++i)
    check(roll->capture().events[i].beat == references[i].beat &&
              roll->capture().events[i].velocity == references[i].velocity,
          "roll keeps raw timing and dynamics");
  Engine::MidiHit midi;
  int notes = 0;
  while (roll->popMidi(midi)) {
    check(midi.note == 36, "provisional MIDI uses the immediate note");
    ++notes;
  }
  check(notes == 3, "late commit does not emit another MIDI note");
  auto tail = std::make_unique<Engine>();
  tail->prepare(48000.);
  tail->setProfile(p);
  tail->arm();
  for (int i = 0; i < 201600; ++i)
    tail->process(input(i / 48000., 3.995), {i / 24000., true, true}, l, r);
  check(tail->loop().count == 1 && tail->loop().events[0].beat < 8. &&
            tail->loop().events[0].voice == 1,
        "last hit across the seam commits to its original take");
  tail->undo();
  check(tail->loop().count == 0, "tail commit is one undo transaction");
  p.lateRejects = 4;
  p.negativeSlices.fill(7);
  for (int i = 0; i < 4; ++i)
    p.negativeLate[i] = p.late[1][0];
  // Move the positive late references away, leaving a decisive negative bank.
  for (int d = 0; d < 2; ++d)
    for (auto &f : p.late[d])
      f.fill(127);
  check(p.classifyLate(slices, 0) == -1,
        "a personal late rejection drops the loop hit");
  std::cout << "PASS: " << checks << " enrollment checks\n";
}
