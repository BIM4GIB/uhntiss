#include "pocket/state.hpp"
#include <cmath>
#include <cstring>
#include <iostream>
#include <memory>
#include <vector>
using namespace pocket;
static int checks = 0;
void check(bool ok, const char *message) {
  ++checks;
  if (!ok) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}
static double input(double t, double onset) {
  const double d = t - onset;
  return d >= 0. && d < .07 ? .6 * std::sin(d * 2. * 3.141592653589793 * 110.) * std::exp(-d * 40.) : 0.;
}
int main() {
  auto profile = std::make_unique<Profile>();
  auto &p = *profile;
  p.voices = maxVoices; p.sampleRate = 48000.; p.scale.fill(3.);
  for (int d = 0; d < maxVoices; ++d) {
    std::snprintf(p.names[d].data(), 25, "voice_%d", d);
    p.notes[d] = 35 + d; p.count[d] = maxExamples; p.lateCount[d] = maxExamples;
    for (int k = 0; k < maxExamples; ++k) { p.examples[d][k].fill(d); p.late[d][k].fill(std::int8_t(d)); }
  }
  StateValues payload{};
  Loop loop; loop.append({.123456789, .37f, Drum::noise, 15, 50});
  const auto length = encodeState(loop, p, payload);
  auto decoded = std::make_unique<Profile>(); Loop restored;
  check(length < 32767 && decodeState(payload.data(), length, restored, *decoded), "maximum v5 state fits a Max list and decodes");
  check(decoded->examples == p.examples && decoded->late == p.late && decoded->names == p.names, "all voices and all examples round-trip");
  check(restored.events[0].note == 50 && restored.events[0].beat == .123456789, "custom MIDI note and pocket survive save");
  const auto valid = payload;
  payload[3 + 5 + 245] = 128;
  check(!decodeState(payload.data(), length, restored, *decoded) && decoded->notes[15] == 50, "bad MIDI note rejects transactionally");
  payload = valid; payload[3 + 5 + 245 + 30 + maxExamples * 17] = 4294967296.;
  check(!decodeState(payload.data(), length, restored, *decoded), "packed features reject overflow");
  Detection custom{Drum::noise, .4f, 0.}; custom.features.fill(15.);
  check(p.classifyVoice(custom) == 15, "factory rejection cannot veto an enrolled voice");
  p.rejects = 1; p.negative[0] = custom.features;
  check(p.classifyVoice(custom) == -1, "explicit negative wins even against an exact positive tie");
  p.rejects = 0; p.radius.fill(.01); custom.features.fill(30.);
  check(p.classifyVoice(custom) == -1, "outside enrollment support is rejected");

  auto extractor = std::make_unique<SliceExtractor>();
  extractor->prepare(48000.);
  Detector detector; detector.prepare(48000.); Detection detected{}; bool got = false;
  for (int i = 0; i < 24000; ++i) {
    const auto x = input(i / 48000., .2); extractor->push(x);
    Detection d{};
    if (detector.process(x, i / 48000., .010, d, Detector::Policy::enrolled) && !got) { detected = d; got = true; }
  }
  check(got, "fixture has a detected onset");
  const auto start = std::int64_t(std::round(detected.beat * 48000.));
  const auto slices = extractor->extract(start, start + 3840);
  const auto clipped = extractor->extract(start, start + 2400);
  check(clipped.samples == std::array<int,3>{480,1440,480}, "50 ms roll truncates tail at its onset");
  const auto before = clipped.values;
  for (int i = 0; i < 1000; ++i) extractor->push(3.);
  check(extractor->extract(start, start + 2400).values == before, "later audio cannot alter a truncated descriptor");
  p = Profile{}; p.voices = 2; p.sampleRate = 48000.; p.scale.fill(3.);
  p.notes[0] = 36; p.notes[1] = 37;
  for (int d = 0; d < 2; ++d) {
    p.count[d] = 3; p.lateCount[d] = 3;
    for (int k = 0; k < 3; ++k) {
      p.examples[d][k] = detected.features;
      if (d) for (auto &f : p.examples[d][k]) f += 10.;
      for (int j = 0; j < 120; ++j) p.late[d][k][j] = d ? std::int8_t(std::clamp(int(std::round(slices.values[j] / 3. * 16)), -128, 127)) : 127;
    }
  }
  check(p.classifyLate(slices, 0) == 1, "later body/tail can revise the provisional voice");
  Slices shortRoll = slices; shortRoll.samples[1] = 100; shortRoll.samples[2] = 0;
  check(p.classifyLate(shortRoll, 0) == 0, "roll before 40 ms keeps early decision instead of comparing unequal windows");
  auto engine = std::make_unique<Engine>(); engine->prepare(48000.); engine->setProfile(p); engine->arm();
  int audible = -1, stored = -1; double l, r;
  for (int i = 0; i < 24000; ++i) {
    engine->process(input(i / 48000., .2), {i / 24000., true, true}, l, r);
    if (audible < 0 && engine->hitSerial()) audible = i;
    if (stored < 0 && engine->capture().count) stored = i;
  }
  check(audible == start + 479, "audible event remains at the ten-millisecond sample");
  check(stored == start + 3839, "loop redecision arrives at 80 ms exactly");
  check(engine->capture().count == 1 && engine->capture().events[0].voice == 1 && engine->capture().events[0].note == 37, "loop stores revised custom voice");
  check(engine->capture().events[0].beat == detected.beat * 2. && engine->capture().events[0].velocity == detected.velocity, "commit preserves exact onset and velocity");
  check(engine->synthTriggerCount() == 1, "late redecision never retriggers the audible kit");
  engine->undo();
  for (int i = 24000; i < 25000; ++i) engine->process(0., {i / 24000., true, true}, l, r);
  check(engine->capture().count == 0 && engine->loop().count == 0, "cancelled capture stays cancelled");
  std::cout << "PASS: " << checks << " enrollment checks\n";
}
