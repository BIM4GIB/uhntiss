#include "pocket/engine.hpp"
#include "pocket/state.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <thread>
#include <vector>
using namespace pocket;
static int checks = 0;
void check(bool ok, const char *name) {
  ++checks;
  if (!ok) {
    std::cerr << "FAIL: " << name << "\n";
    std::exit(1);
  }
}
struct Rig {
  Engine e;
  double beat = 0., sr = 48000., bpm = 120.;
  Rig() { e.prepare(sr); }
  void sample(bool running = true, bool four = true, double input = 0.) {
    double l, r;
    e.process(input, {beat, running, four}, l, r);
    checkFinite(l, r);
    if (running)
      beat += bpm / (60. * sr);
  }
  void checkFinite(double l, double r) {
    if (!std::isfinite(l) || !std::isfinite(r) || std::abs(l) > 1. ||
        std::abs(r) > 1.)
      check(false, "finite bounded output");
  }
  void to(double end) {
    while (beat < end)
      sample();
  }
  void record(double offset = .061) {
    e.arm();
    to(4. + offset);
    e.tap(Drum::snare, .47f);
    to(12.05);
  }
};
int main() {
  // Separate test frames keep large fixed audio buffers from accumulating
  // in one Debug/ASan stack frame. Runtime engines remain allocation-free.
  [] {
    Loop before;
    before.anchor = 17.125;
    before.append({.07123, .37f, Drum::hat});
    Profile learned;
    for (int d = 0; d < 3; ++d)
      for (int k = 0; k < 3; ++k) {
        Features f{};
        f.fill(d * 1.7 + k * .2);
        learned.add(Drum(d), f);
      }
    StateValues values{};
    const auto size = encodeState(before, learned, values);
    Loop after;
    Profile restored;
    check(decodeState(values.data(), size, after, restored),
          "v4 loop/profile state decodes");
    check(after.anchor == before.anchor &&
              after.events[0].beat == before.events[0].beat &&
              after.events[0].velocity == before.events[0].velocity,
          "state preserves pocket and dynamics exactly");
    check(restored.ready() && restored.examples == learned.examples,
          "all nine voice examples survive persistence");
    values[3] = NAN;
    check(!decodeState(values.data(), size, after, restored) &&
              after.count == 1,
          "corrupt state is atomic");
    std::array<double, 21> old{2, 0,  1,  .07123, .37, 2, 3,  .1, .2, .3, .4,
                               3, .2, .3, .4,     .5,  3, .3, .4, .5, .6};
    check(decodeState(old.data(), old.size(), after, restored) &&
              after.count == 1 && after.events[0].drum == Drum::hat &&
              !restored.ready(),
          "v2 migrates loop and resets incompatible voice centroids");
    std::array<double, 135> v3{};
    v3[0] = 3.;
    v3[1] = 4.125;
    v3[2] = 1.;
    v3[3] = .07123;
    v3[4] = .37;
    v3[5] = 2.;
    v3[6] = v3[49] = v3[92] = 3.;
    check(decodeState(v3.data(), v3.size(), after, restored) &&
              after.anchor == 4.125 && after.events[0].beat == .07123 &&
              after.events[0].drum == Drum::hat && !restored.ready(),
          "v3 migrates pocket exactly and resets obsolete attack descriptors");
    Detection rejected{Drum::noise, .1f, 0., {}};
    check(learned.classify(rejected) == Drum::noise,
          "personal profile preserves factory noise rejection");
    old[5] = 2.5;
    check(!decodeState(old.data(), old.size(), after, restored),
          "fractional drum id rejected");
    Loop quiet;
    quiet.append({.12, .02f, Drum::hat});
    const auto quietSize = encodeState(quiet, {}, values);
    check(decodeState(values.data(), quietSize, after, restored) &&
              after.events[0].velocity == .02f,
          "minimum float velocity survives persistence");
  }();

  for (double sr : {44100., 48000., 96000.}) {
    Engine e;
    e.prepare(sr);
    Settings settings;
    settings.voice = 1;
    e.setSettings(settings);
    e.arm();
    double first = -1.;
    for (int i = 0; i < int(sr * 4.05); ++i) {
      double t = i / sr;
      double input =
          t > .05 && t < .15
              ? .7 * std::sin(2. * 3.141592653589793 * 95. * (t - .05)) *
                    std::exp(-(t - .05) * 35.)
              : 0.;
      double l, r;
      e.process(input, {t * 2., true, true}, l, r);
      if (first < 0 && std::abs(l) > 1e-7)
        first = t;
      if (t > .1 && t < .1001)
        check(e.synthTriggerCount() == 1,
              "fixed input refines one voice without retriggering");
    }
    check(first >= .05 && first - .05 < .003,
          "fixed-voice monitoring starts within 3ms of test pulse");
    check(e.loop().count == 1 && e.loop().events[0].drum == Drum::snare,
          "immediate fixed voice captures one correctly labelled event");
    check(e.loop().events[0].velocity > .5f,
          "loop keeps refined attack velocity");
  }

  // The quiet Auto path must never alter manual/Teach or the research
  // candidate stream, including after a policy change mid-performance.
  for (auto policy :
       {Detector::Policy::fixedVoice, Detector::Policy::candidates}) {
    Detector detector;
    AttackTracker reference;
    detector.prepare(48000.);
    reference.prepare(48000.);
    int completions = 0;
    for (int i = 0; i < 48000; ++i) {
      const double t = i / 48000.;
      const double within = std::fmod(t, .13);
      const double input =
          within < .04
              ? .03 * std::sin(within * 2. * 3.141592653589793 * 3100.) *
                    std::exp(-within * 80.)
              : 0.;
      Detection actual{}, expected{};
      const bool a = detector.process(input, t, .010, actual, policy);
      const bool b = reference.process(input, t, .010, expected, policy);
      if (a != b || detector.started() != reference.started())
        check(false, "Auto recovery leaked into single-tracker policy");
      if (a) {
        ++completions;
        if (actual.beat != expected.beat ||
            actual.velocity != expected.velocity ||
            actual.features != expected.features)
          check(false, "single-tracker timing/dynamics changed");
      }
    }
    check(completions >= 6,
          "single-tracker test exercised real attack windows");
  }

  [] {
    Rig r;
    r.e.learn(1);
    r.e.undo();
    check(r.e.learning() == -1,
          "undo exits teaching without touching loop history");
  }();

  [] {
    HostClock c;
    double ppq = 0.;
    for (int block = 0; block < 4000; ++block) {
      double b = block * 64. / 24000.;
      if (block % 700 == 0)
        ppq = b;
      c.begin(ppq, b - std::floor(b), true, true);
      for (int i = 0; i < 64; ++i) {
        double actual = (block * 64. + i) / 24000.;
        auto v = c.next(actual - std::floor(actual));
        if (std::abs(v.beat - actual) > 1e-9)
          check(false, "audio phase survives sparse PPQ messages");
      }
    }
    check(true, "host phase unwrap across blocks");
    c.begin(100., 0., true, true);
    check(c.next(0.).beat == 100., "host locate reanchors clock");
    c.begin(42.5, 0., false, true);
    check(!c.next(0.).running && c.next(0.).beat == 42.5,
          "stopped clock uses host PPQ");
  }();

  [] {
    Loop l;
    check(l.append({.7, .5f, Drum::hat}), "append");
    l.append({.1, .9f, Drum::kick});
    check(l.events[0].beat == .1, "chronological order");
    check(!l.append({8., .5f, Drum::hat}), "exclusive loop end");
    check(!l.append({NAN, .5f, Drum::hat}), "NaN rejected");
    for (size_t i = l.count; i < maxEvents; ++i)
      l.append({.2, .4f, Drum::snare});
    check(!l.append({.3, .4f, Drum::hat}), "bounded event capacity");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    check(r.e.loop().count == 1, "one hit committed");
    check(std::abs(r.e.loop().events[0].beat - .061) < .0001,
          "keeps 30.5ms pocket at 120 BPM");
    check(std::abs(r.e.loop().events[0].velocity - .47f) < 1e-7,
          "keeps velocity");
    check(r.e.undoCount() == 1, "one transaction");
    r.e.clear();
    check(r.e.loop().count == 0, "clear");
    r.e.undo();
    check(r.e.loop().count == 1, "undo clear");
    r.e.undo();
    check(r.e.loop().count == 0, "undo recording");
  }();
  [] {
    Rig r;
    r.e.arm();
    r.sample(false);
    check(r.e.state() == State::armed, "arm while stopped");
    r.sample();
    check(r.e.state() == State::recording,
          "record on transport start downbeat");
    r.e.tap(Drum::kick);
    r.to(8.05);
    check(r.e.loop().count == 1, "stopped arm captures two bars");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    r.e.arm();
    r.to(16.1);
    r.e.tap(Drum::kick);
    r.e.undo();
    check(r.e.loop().count == 1 && r.e.state() == State::playing,
          "undo cancels recording, preserves loop");
    check(r.e.undoCount() == 1, "cancel consumes no undo");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    Settings s = r.e.settings();
    s.mode = Mode::overdub;
    r.e.setSettings(s);
    r.e.arm();
    r.to(16.35);
    r.e.tap(Drum::hat, .23f);
    r.to(24.05);
    check(r.e.loop().count == 2, "overdub retains original");
    check(std::abs(r.e.loop().events[1].beat - 4.061) < .0001,
          "overdub preserves absolute loop phase across alternate bar");
    r.e.undo();
    check(r.e.loop().count == 1, "undo overdub");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    r.e.arm();
    r.to(24.05);
    check(r.e.loop().count == 1 && r.e.state() == State::empty,
          "silent take preserves previous loop");
    check(r.e.undoCount() == 1, "silent take does not consume undo");
  }();
  [] {
    Rig r;
    r.e.arm();
    r.sample();
    r.e.tap(Drum::kick);
    r.to(8.001);
    r.e.hit(Drum::hat, .5f, 7.999);
    r.to(8.05);
    check(r.e.loop().count == 2, "late classification belongs to prior take");
    check(r.e.undoCount() == 1, "tail stays in same transaction");
  }();
  [] {
    Rig r;
    r.e.arm();
    r.sample();
    r.e.tap(Drum::kick);
    r.to(8.001);
    r.e.undo();
    check(r.e.loop().count == 0, "undo in seam tail undoes committed loop");
  }();
  [] {
    Rig r;
    r.e.arm();
    r.sample();
    r.to(8.001);
    r.e.hit(Drum::hat, .5f, 7.999);
    r.to(8.05);
    check(r.e.loop().count == 1, "only hit in analysis tail is kept");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    r.e.arm();
    r.to(16.5);
    r.e.tap(Drum::kick);
    r.beat = 2.;
    r.sample();
    check(r.e.loop().count == 1 && r.e.state() == State::interrupted,
          "seek discards partial take");
    r.e.arm();
    r.sample(true, false);
    check(r.e.state() == State::unsupportedMeter,
          "unsupported meter is explicit");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    r.e.arm();
    r.to(16.5);
    r.sample(false);
    check(r.e.state() == State::interrupted && r.e.loop().count == 1,
          "stop preserves committed loop");
  }();
  [] {
    Rig r;
    r.e.arm();
    r.sample();
    for (size_t i = 0; i <= maxEvents; ++i)
      r.e.tap(Drum::hat);
    check(r.e.state() == State::full && r.e.loop().count == 0,
          "overflow rolls back take");
  }();
  [] {
    Rig r;
    Loop l;
    l.append({.1, .8f, Drum::kick});
    for (int i = 0; i < 22; ++i)
      r.e.restore(l);
    check(r.e.undoCount() == undoDepth, "bounded history");
    for (int i = 0; i < 22; ++i)
      r.e.undo();
    check(r.e.undoCount() == 0, "history exhaustion safe");
  }();
  [] {
    for (double sr : {44100., 48000., 96000.}) {
      Detector d;
      d.prepare(sr);
      Detection hit{};
      int first = -1, count = 0;
      double onset = 0.;
      for (int i = 0; i < int(sr * .35); ++i) {
        double t = i / sr;
        double x =
            t >= .05 && t < .18
                ? .7 * std::sin(2. * 3.141592653589793 * 95. * (t - .05)) *
                      std::exp(-(t - .05) * 35.)
                : 0.;
        if (d.process(x, t * 2., .018, hit)) {
          first = i;
          onset = hit.beat;
          count++;
          // A sine pulse tests detection and latency. Beatbox class accuracy
          // is tested with held-out vocal attacks, not a synthetic bass tone.
        }
      }
      check(count == 1, "one attack, no retrigger on decay");
      check(first / sr - .05 < .012, "decision latency below 12ms on pulse");
      check(onset / 2. - .05 < .004,
            "onset timestamp excludes analysis latency");
    }
  }();
  [] {
    Rig r;
    Loop loop;
    loop.append({.061, .7f, Drum::snare});
    loop.append({7.999, .4f, Drum::hat});
    r.e.restore(loop);
    uint64_t serial = 0;
    std::vector<double> heard;
    while (r.beat < 24.) {
      r.sample();
      if (r.e.playbackSerial() != serial) {
        serial = r.e.playbackSerial();
        heard.push_back(r.e.lastPlaybackBeat());
      }
      if (r.beat > 9.)
        r.bpm = 173.;
    }
    check(heard.size() == 6,
          "one trigger per event per cycle across tempo change");
    for (size_t i = 0; i < heard.size(); ++i)
      check(std::abs(heard[i] - (8. * (i / 2) + (i % 2 ? 7.999 : .061))) <
                173. / (60. * r.sr),
            "playback error below one sample");
    r.beat = 200.;
    r.sample();
    check(r.e.playbackSerial() == 6, "seek does not burst old events");
  }();
  [] {
    Rig r;
    r.sample();
    r.record();
    Settings s = r.e.settings();
    s.mode = Mode::overdub;
    r.e.setSettings(s);
    r.e.arm();
    r.to(24.05);
    check(r.e.undoCount() == 1, "silent overdub does not consume history");
  }();
  [] {
    Profile p;
    for (int d = 0; d < 3; ++d)
      for (int i = 0; i < 3; ++i)
        p.add(Drum(d), {d * .4, 1. - d * .4, d * .3, .3});
    check(p.ready(), "three examples per voice activates profile");
    Detection d{Drum::kick, .5f, 0., {.42, .58, .32, .3}};
    check(p.classify(d) == Drum::snare,
          "profile classifies independent of fallback");
  }();
  [] {
    Rig r;
    Settings s;
    s.voice = 1;
    r.e.setSettings(s);
    for (int i = 0; i < 10000; ++i) {
      double t = i / r.sr;
      double x = t > .05 && t < .15
                     ? .7 * std::sin(2. * 3.141592653589793 * 95. * (t - .05)) *
                           std::exp(-(t - .05) * 35.)
                     : 0.;
      r.sample(false, true, x);
    }
    check(r.e.lastDrum() == 1, "fixed voice bypasses classifier");
  }();
  [] {
    Rig r;
    r.e.learn(2);
    for (int i = 0; i < 48000; ++i) {
      double t = std::fmod(i / r.sr, .3);
      double x = t > .05 && t < .15
                     ? .7 * std::sin(2. * 3.141592653589793 * 95. * (t - .05)) *
                           std::exp(-(t - .05) * 35.)
                     : 0.;
      r.sample(false, true, x);
    }
    check(r.e.profile().count[2] == 3 && r.e.learning() == -1,
          "teaching captures three attacks then stops");
    check(r.e.loop().count == 0, "teaching never records a loop");
  }();
  [] {
    Queue<int, 64> q;
    constexpr int n = 100000;
    std::thread producer([&] {
      for (int i = 0; i < n; ++i)
        while (!q.push(i))
          std::this_thread::yield();
    });
    for (int i = 0; i < n; ++i) {
      int v;
      while (!q.pop(v))
        std::this_thread::yield();
      check(v == i, "SPSC order");
    }
    producer.join();
  }();
  [] {
    Rig r;
    Settings s;
    r.e.setSettings(s);
    for (int i = 0; i < 48000; ++i) {
      if (i % 211 == 0)
        r.e.tap(Drum(i % 3), 1.f);
      r.sample();
    }
    check(true, "dense polyphony finite and bounded");
  }();
  std::cout << "PASS: " << checks << " checks\n";
}
