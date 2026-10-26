#pragma once
#include "pocket/slices.hpp"
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace pocket {
constexpr int maxVoices = 16, maxExamples = 30, maxRejects = 30,
              featureVersion = 4;
constexpr std::size_t maxEvents = 512;
constexpr std::size_t undoDepth = 16;
constexpr double loopBeats = 8.; // Deliberate first release: two bars of 4/4.
enum class Drum : int { kick, snare, hat, noise };
enum class Mode : int { replace, overdub };
enum class State : int {
  live,
  armed,
  recording,
  playing,
  empty,
  full,
  unsupportedMeter,
  interrupted
};
struct Event {
  double beat = 0.;
  float velocity = .8f;
  Drum drum = Drum::kick;
  int voice = -1; // -1 for legacy pad events; otherwise enrollment index.
  int note =
      -1; // Stored with the hit: profile remapping cannot retune an old loop.
};
struct Loop {
  std::array<Event, maxEvents> events{};
  std::size_t count = 0;
  double anchor = 0.;
  bool append(Event event) noexcept;
};
struct Settings {
  double inputGain = 1.;
  double outputGain = .75;
  double threshold = .010;
  double tone = .5;
  double decay = .5;
  double drive = .25;
  bool monitor = true;
  bool playback = true;
  Mode mode = Mode::replace;
  int voice = -1; // -1 automatic; 0/1/2 force a voice for dependable layering.
};
struct Clock {
  double beat = 0.;
  bool running = false;
  bool fourFour = true;
};
// Audio-rate phase advances even if UI/scheduler messages pause. Fresh PPQ
// messages anchor it and expose host seeks; they never drive individual hits.
class HostClock {
public:
  void begin(double ppq, double firstPhase, bool running,
             bool fourFour) noexcept;
  Clock next(double phase) noexcept;
  void reset() noexcept { valid_ = false; }

private:
  double base_ = 0., previous_ = 0., host_ = 0.;
  bool valid_ = false, running_ = false, fourFour_ = true;
};
using Features = std::array<double, 17>;
using TransientFeatures = std::array<double, 23>;
struct Detection {
  Drum drum;
  float velocity;
  double beat;
  Features features{};
  std::array<double, 6> bandContrast{};
};
// The factory model contains causal attack descriptors, never audio or a
// rhythm grid. A decision cannot move, invent or quantize an onset.
Drum classifyAttack(const Features &features,
                    double sampleRate = 48000.) noexcept;
Drum classifyTransientAttack(const TransientFeatures &features) noexcept;
struct Profile {
  int metricVersion = featureVersion;
  // Derived once from the imported bank, outside DSP; not a new wire payload.
  std::array<std::array<double, 41>, maxVoices + 1> noiseScores{};
  bool noiseReady = false;
  void prepareRejector() noexcept;
  bool rejectsAttack(const Slices &features) const noexcept;
  int voices = 3, rejects = 0, lateRejects = 0;
  double sampleRate = 0.; // Zero: v4 compatibility, early-only Teach.
  std::array<std::array<Features, maxExamples>, maxVoices> examples{};
  std::array<int, maxVoices> count{};
  std::array<std::array<char, 25>, maxVoices> names{
      {{{'k', 'i', 'c', 'k'}}, {{'s', 'n', 'a', 'r', 'e'}}, {{'h', 'a', 't'}}}};
  std::array<int, maxVoices> notes{{36, 38, 42}};
  std::array<std::array<std::array<std::int8_t, 120>, maxExamples>, maxVoices>
      late{};
  std::array<int, maxVoices> lateCount{};
  SliceFeatures scale{}, center{};
  std::array<double, maxVoices>
      radius{}; // Early squared distance; 0 disables open-set radius.
  std::array<Features, maxRejects> negative{};
  std::array<std::array<std::int8_t, 120>, maxRejects> negativeLate{};
  std::array<int, maxRejects> negativeSlices{};
  void add(Drum drum, const Features &features) noexcept;
  Drum classify(const Detection &detection) const noexcept; // v4 API
  int classifyVoice(const Detection &detection,
                    const Slices *rich = nullptr) const noexcept; // -1 rejects
  int classifyLate(const Slices &features, int provisional) const noexcept;
  bool ready() const noexcept {
    if (voices < 2 || voices > maxVoices)
      return false;
    for (int d = 0; d < voices; ++d)
      if (count[d] < 3 || count[d] > maxExamples)
        return false;
    return true;
  }
  bool delayed() const noexcept {
    if (!ready() || sampleRate <= 0.)
      return false;
    for (int d = 0; d < voices; ++d)
      if (lateCount[d] < 3 || lateCount[d] > maxExamples)
        return false;
    return true;
  }
};
// One causal 10 ms attack window with fixed storage. Auto adds quiet-hat and
// 48 kHz band-transient trackers; forced voices and Teach still use one.
class AttackTracker {
public:
  // candidates is an offline research policy: collect a model-independent
  // superset of attacks. Engine enrollment uses a separate causal policy.
  enum class Policy {
    automatic,
    fixedVoice,
    candidates,
    quiet,
    transientCandidates,
    enrolled
  };
  void prepare(double sampleRate, bool transient = false) noexcept;
  bool process(double input, double beat, double threshold, Detection &event,
               Policy policy = Policy::automatic) noexcept;
  bool started() const noexcept { return started_; }
  float attackVelocity() const noexcept;
  bool pending() const noexcept { return collect_ > 0; }
  double pendingBeat() const noexcept { return onsetBeat_; }
  int pendingAge() const noexcept { return collect_ > 0 ? count_ : 0; }
  double level() const noexcept { return fast_; }
  void reset() noexcept;

private:
  Features descriptor() const noexcept;
  double sr_ = 48000., hp_ = 0., dc_ = 0.;
  double fast_ = 0., slow_ = 0., previous_ = 0., onsetBeat_ = 0.;
  double total_ = 0., peak_ = 0.;
  // Thirty milliseconds of past power, bounded even at 384 kHz. This adds
  // context, not lookahead. The classification deadline is still 10 ms.
  std::array<double, 11520> history_{};
  double historySum_ = 0., onsetPower_ = 0., earlyPower_ = 0.;
  double onsetDc_ = 0., onsetCoeff_ = 0., onsetPeak_ = 0., rejectedPeak_ = 0.;
  int historyIndex_ = 0, historyCount_ = 0, historySize_ = 1440;
  std::array<double, 5> bands_{}, bandCoeffs_{};
  std::array<std::array<double, 6>, 2> energy_{};
  std::array<double, 6> bandFast_{}, bandSlow_{}, context_{}, prior_{};
  double contextCoeff_ = 0.;
  bool transient_ = false;
  double dcCoeff_ = 0., attack_ = 0., release_ = 0., slowCoeff_ = 0.;
  int collect_ = 0, window_ = 480, refractory_ = 0, count_ = 0, crossings_ = 0,
      quiet_ = 0, releaseSamples_ = 0;
  bool released_ = true, started_ = false;
};
class Detector {
public:
  using Policy = AttackTracker::Policy;
  void prepare(double sr) noexcept;
  bool process(double input, double beat, double threshold, Detection &event,
               Policy policy = Policy::automatic) noexcept;
  bool started() const noexcept { return primary_.started(); }
  bool boundary() const noexcept { return boundary_; }
  // Synchronous acknowledgement of the candidate just returned by process.
  // Restore the previous accepted onset; never erase its exclusion window.
  void rejectDecision() noexcept { lastAccepted_ = previousAccepted_; }
  int pendingAge() const noexcept;
  float attackVelocity() const noexcept { return primary_.attackVelocity(); }
  bool pending() const noexcept { return primary_.pending(); }
  double pendingBeat() const noexcept { return primary_.pendingBeat(); }
  double level() const noexcept { return primary_.level(); }
  void reset() noexcept;

private:
  // Every tracker sees raw input; recovery never applies an audio gain boost.
  AttackTracker primary_{}, quiet_{}, transient_{};
  bool transientEnabled_ = false, boundary_ = false;
  std::int64_t sample_ = 0, lastAccepted_ = -1000000,
               previousAccepted_ = -1000000;
  int refractory_ = 1680, window_ = 480;
  Policy previousPolicy_ = Policy::automatic;
};
class Kit {
public:
  void prepare(double sampleRate) noexcept;
  std::uint64_t trigger(Drum drum, float velocity,
                        const Settings &settings) noexcept;
  void refine(std::uint64_t id, float velocity) noexcept;
  std::uint64_t triggerCount() const noexcept { return serial_ - 1; }
  std::array<double, 2> process(const Settings &settings) noexcept;
  void reset() noexcept;

private:
  struct Voice {
    Drum drum = Drum::kick;
    double env = 0., body = 0., phase = 0., phase2 = 0., freq = 0., pitch = 0.,
           decay = 0., bodyDecay = 0., filter = 0., filterCoeff = 0.,
           velocity = 0., targetVelocity = 0.;
    std::uint64_t id = 0;
    std::uint32_t noise = 1;
  };
  std::array<Voice, 24> voices_{};
  double sr_ = 48000., dcL_ = 0., dcR_ = 0.;
  std::uint64_t serial_ = 1;
};
// Engine belongs exclusively to the audio thread. All memory has fixed bounds.
class Engine {
public:
  void prepare(double sampleRate) noexcept;
  void setSettings(const Settings &settings) noexcept { settings_ = settings; }
  const Settings &settings() const noexcept { return settings_; }
  void arm() noexcept;
  void cancel() noexcept;
  void undo() noexcept;
  void clear() noexcept;
  void tap(Drum drum, float velocity = .85f) noexcept;
  void restore(const Loop &loop) noexcept;
  void learn(int drum) noexcept;
  const Profile &profile() const noexcept { return profile_; }
  void setProfile(const Profile &profile) noexcept {
    cancel();
    detector_.reset();
    profile_ = profile;
    learning_ = -1;
    ++revision_;
  }
  int learning() const noexcept { return learning_; }
  int learnRemaining() const noexcept {
    return learning_ < 0 ? 0 : 3 - profile_.count[learning_];
  }
  void process(double input, Clock clock, double &left, double &right) noexcept;
  // Timestamped trigger also makes MIDI/pad input follow the same capture path.
  void hit(Drum drum, float velocity, double onsetBeat,
           bool sound = true) noexcept;
  struct MidiHit {
    int note = 0;
    float velocity = 0.f;
  };
  bool popMidi(MidiHit &hit) noexcept;
  void hitVoice(int voice, float velocity, double onsetBeat,
                bool sound = true) noexcept;
  const Loop &loop() const noexcept { return active_; }
  const Loop &capture() const noexcept { return take_; }
  State state() const noexcept { return state_; }
  std::size_t undoCount() const noexcept { return historyCount_; }
  std::uint64_t revision() const noexcept { return revision_; }
  double position() const noexcept;
  double level() const noexcept { return detector_.level(); }
  bool running() const noexcept { return clock_.running; }
  int lastDrum() const noexcept { return lastDrum_; }
  std::uint64_t playbackSerial() const noexcept { return playbackSerial_; }
  double lastPlaybackBeat() const noexcept { return lastPlaybackBeat_; }
  std::uint64_t synthTriggerCount() const noexcept {
    return kit_.triggerCount();
  }
  std::uint64_t hitSerial() const noexcept { return hitSerial_; }
  double lastHitBeat() const noexcept { return lastHitBeat_; }

private:
  void saveUndo() noexcept;
  void store(Event event, double beat) noexcept;
  void sound(Event event) noexcept;
  Event voiceEvent(int voice, float velocity) const noexcept;
  void finishDecision() noexcept;
  void commit() noexcept;
  void resetSchedule() noexcept { scheduleValid_ = false; }
  void schedule(double beat) noexcept;
  Settings settings_{};
  Profile profile_{};
  int learning_ = -1, earlyDrum_ = -1;
  std::uint64_t earlyVoice_ = 0;
  Clock clock_{};
  Detector detector_{};
  Kit kit_{};
  SliceExtractor slices_{};
  struct Pending {
    Detection hit{};
    int voice = -1;
    std::int64_t start = 0, end = 0;
    bool active = false;
  } pending_{};
  std::array<MidiHit, 1024> midi_{};
  int midiRead_ = 0, midiWrite_ = 0;
  Loop active_{}, take_{};
  std::array<Loop, undoDepth> history_{};
  std::size_t historyCount_ = 0, historyHead_ = 0, takeAdded_ = 0;
  State state_ = State::live;
  double sr_ = 48000., captureStart_ = 0., previousBeat_ = 0.,
         inputSmooth_ = 1., outputSmooth_ = .75;
  bool clockValid_ = false, scheduleValid_ = false, waitingForBoundary_ = false,
       recording_ = false, tailOpen_ = false, committed_ = false;
  std::uint64_t revision_ = 0, hitSerial_ = 0, playbackSerial_ = 0;
  double lastHitBeat_ = 0.;
  double lastPlaybackBeat_ = 0., scheduleCycle_ = 0.;
  size_t nextEvent_ = 0;
  int tailSamples_ = 0, lastDrum_ = -1;
};
// One producer / one consumer. The Max adapter serializes message-thread
// producers outside the audio callback. Neither push nor pop allocates or
// waits.
template <class T, std::size_t N> class Queue {
public:
  bool push(const T &item) noexcept {
    const auto w = write_.load(std::memory_order_relaxed), next = (w + 1) % N;
    if (next == read_.load(std::memory_order_acquire))
      return false;
    items_[w] = item;
    write_.store(next, std::memory_order_release);
    return true;
  }
  bool pop(T &item) noexcept {
    const auto r = read_.load(std::memory_order_relaxed);
    if (r == write_.load(std::memory_order_acquire))
      return false;
    item = items_[r];
    read_.store((r + 1) % N, std::memory_order_release);
    return true;
  }

private:
  std::array<T, N> items_{};
  std::atomic<std::size_t> read_{0}, write_{0};
};
} // namespace pocket
