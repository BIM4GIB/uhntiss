#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace pocket {
constexpr std::size_t maxEvents = 512;
constexpr std::size_t undoDepth = 16;
constexpr double loopBeats = 8.; // Deliberate first release: two bars of 4/4.
enum class Drum : int { kick, snare, hat };
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
using Features = std::array<double, 14>;
struct Detection {
  Drum drum;
  float velocity;
  double beat;
  Features features{};
};
// The factory model contains causal attack descriptors, never audio or a
// rhythm grid. A decision cannot move, invent or quantize an onset.
Drum classifyAttack(const Features &features) noexcept;
struct Profile {
  std::array<std::array<Features, 3>, 3> examples{};
  std::array<int, 3> count{};
  void add(Drum drum, const Features &features) noexcept;
  Drum classify(const Detection &detection) const noexcept;
  bool ready() const noexcept {
    return count[0] >= 3 && count[1] >= 3 && count[2] >= 3;
  }
};
class Detector {
public:
  void prepare(double sampleRate) noexcept;
  bool process(double input, double beat, double threshold,
               Detection &event) noexcept;
  bool started() const noexcept { return started_; }
  float attackVelocity() const noexcept;
  bool pending() const noexcept { return collect_ > 0; }
  double pendingBeat() const noexcept { return onsetBeat_; }
  double level() const noexcept { return fast_; }
  void reset() noexcept;

private:
  double sr_ = 48000., hp_ = 0., dc_ = 0.;
  double fast_ = 0., slow_ = 0., previous_ = 0., onsetBeat_ = 0.;
  double total_ = 0., peak_ = 0.;
  std::array<double, 5> bands_{}, bandCoeffs_{};
  std::array<std::array<double, 6>, 2> energy_{};
  double dcCoeff_ = 0., attack_ = 0.,
         release_ = 0., slowCoeff_ = 0.;
  int collect_ = 0, window_ = 480, refractory_ = 0, count_ = 0, crossings_ = 0,
      quiet_ = 0, releaseSamples_ = 0;
  bool released_ = true, started_ = false;
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

private:
  void saveUndo() noexcept;
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
  Loop active_{}, take_{};
  std::array<Loop, undoDepth> history_{};
  std::size_t historyCount_ = 0, historyHead_ = 0, takeAdded_ = 0;
  State state_ = State::live;
  double sr_ = 48000., captureStart_ = 0., previousBeat_ = 0.,
         inputSmooth_ = 1., outputSmooth_ = .75;
  bool clockValid_ = false, scheduleValid_ = false, waitingForBoundary_ = false,
       recording_ = false, tailOpen_ = false, committed_ = false;
  std::uint64_t revision_ = 0, hitSerial_ = 0, playbackSerial_ = 0;
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
