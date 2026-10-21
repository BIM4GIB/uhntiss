#include "pocket/engine.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace pocket {
namespace {
constexpr double pi = 3.14159265358979323846;
double clamp(double x, double lo, double hi) noexcept {
  return std::clamp(x, lo, hi);
}
double phase(double x) noexcept {
  return x - std::floor(x / loopBeats) * loopBeats;
}
double coeff(double hz, double sr) noexcept {
  return 1. - std::exp(-2. * pi * hz / sr);
}
} // namespace
void HostClock::begin(double ppq, double first, bool running,
                      bool fourFour) noexcept {
  if (!std::isfinite(ppq) || !std::isfinite(first)) {
    valid_ = false;
    running_ = false;
    return;
  }
  running_ = running;
  fourFour_ = fourFour;
  if (!running) {
    valid_ = false;
    host_ = ppq;
    return;
  }
  if (!valid_) {
    base_ = std::round(ppq - first);
    previous_ = first;
    valid_ = true;
  } else if (ppq != host_) {
    const double expected = base_ + (first < previous_ - .5 ? 1. : 0.);
    const double anchored = std::round(ppq - first);
    if (std::abs(anchored - expected) > .5) {
      base_ = anchored;
      previous_ = first;
    }
  }
  host_ = ppq;
}
Clock HostClock::next(double p) noexcept {
  if (!running_ || !std::isfinite(p))
    return {host_, false, fourFour_};
  if (p < previous_ - .5)
    base_ += 1.;
  previous_ = p;
  return {base_ + p, true, fourFour_};
}
bool Loop::append(Event e) noexcept {
  if (count == maxEvents || !std::isfinite(e.beat) ||
      !std::isfinite(e.velocity) || e.beat < 0. || e.beat >= loopBeats ||
      int(e.drum) < 0 || int(e.drum) > 2)
    return false;
  e.velocity = float(clamp(e.velocity, .02, 1.));
  auto i = count++;
  while (i > 0 && events[i - 1].beat > e.beat) {
    events[i] = events[i - 1];
    --i;
  }
  events[i] = e;
  return true;
}
void Profile::add(Drum drum, const std::array<double, 4> &features) noexcept {
  const auto d = int(drum);
  if (d < 0 || d > 2)
    return;
  const int n = std::min(++count[d], 100);
  count[d] = n;
  for (size_t i = 0; i < 4; ++i)
    mean[d][i] += (features[i] - mean[d][i]) / n;
}
Drum Profile::classify(const Detection &detection) const noexcept {
  if (!ready())
    return detection.drum;
  double closest = 1e9;
  int best = 0;
  for (int d = 0; d < 3; ++d) {
    double distance = 0.;
    for (size_t i = 0; i < 4; ++i) {
      const double delta = detection.features[i] - mean[d][i];
      distance += delta * delta;
    }
    if (distance < closest) {
      closest = distance;
      best = d;
    }
  }
  return Drum(best);
}
void Detector::prepare(double sr) noexcept {
  sr_ = sr;
  window_ = int(std::round(.008 * sr));
  lowCoeff_ = coeff(420., sr);
  midCoeff_ = coeff(3000., sr);
  dcCoeff_ = coeff(25., sr);
  attack_ = 1. - std::exp(-1. / (.0006 * sr));
  release_ = 1. - std::exp(-1. / (.006 * sr));
  slowCoeff_ = 1. - std::exp(-1. / (.045 * sr));
  reset();
}
void Detector::reset() noexcept {
  hp_ = dc_ = low_ = mid_ = fast_ = slow_ = previous_ = total_ = lowEnergy_ =
      highEnergy_ = peak_ = 0.;
  collect_ = refractory_ = count_ = crossings_ = 0;
  released_ = true;
}
bool Detector::process(double x, double beat, double threshold,
                       Detection &result) noexcept {
  dc_ += dcCoeff_ * (x - dc_);
  hp_ = x - dc_;
  low_ += lowCoeff_ * (hp_ - low_);
  mid_ += midCoeff_ * (hp_ - mid_);
  const auto a = std::abs(hp_);
  fast_ += (a > fast_ ? attack_ : release_) * (a - fast_);
  slow_ += slowCoeff_ * (a - slow_);
  if (refractory_ > 0)
    --refractory_;
  if (fast_ < threshold * .5 || fast_ < slow_ * .8)
    released_ = true;
  if (!collect_ && !refractory_ && fast_ > threshold && fast_ > slow_ * 1.65 &&
      released_) {
    collect_ = window_;
    count_ = crossings_ = 0;
    total_ = lowEnergy_ = highEnergy_ = peak_ = 0.;
    onsetBeat_ = beat;
    released_ = false;
    refractory_ = int(.024 * sr_);
  }
  bool detected = false;
  if (collect_) {
    total_ += hp_ * hp_;
    lowEnergy_ += low_ * low_;
    highEnergy_ += (hp_ - mid_) * (hp_ - mid_);
    peak_ = std::max(peak_, a);
    crossings_ += (hp_ * previous_ < 0.);
    ++count_;
    if (!--collect_) {
      const double lowRatio = lowEnergy_ / (total_ + 1e-15),
                   highRatio = highEnergy_ / (total_ + 1e-15);
      const double zcr = double(crossings_) / double(count_);
      const Drum drum =
          (lowRatio > .40 && zcr < .13)
              ? Drum::kick
              : ((highRatio > .48 && zcr > .24) ? Drum::hat : Drum::snare);
      const float velocity = float(clamp(std::pow(peak_, .55), .08, 1.));
      result = {drum,
                velocity,
                onsetBeat_,
                {clamp(lowRatio, 0., 1.), clamp(highRatio, 0., 1.),
                 clamp(zcr * 2., 0., 1.),
                 std::sqrt(total_ / count_) / (peak_ + 1e-12)}};
      detected = true;
    }
  }
  previous_ = hp_;
  return detected;
}
void Kit::prepare(double sr) noexcept {
  sr_ = sr;
  reset();
}
void Kit::reset() noexcept {
  voices_ = {};
  dcL_ = dcR_ = 0.;
  serial_ = 1;
}
void Kit::trigger(Drum drum, float velocity, const Settings &s) noexcept {
  if (int(drum) < 0 || int(drum) > 2 || !std::isfinite(velocity))
    return;
  auto *v = &voices_[0];
  for (auto &voice : voices_) {
    if (voice.env < 1e-5) {
      v = &voice;
      break;
    }
    if (voice.env < v->env)
      v = &voice;
  }
  *v = Voice{};
  v->drum = drum;
  v->env = 1.;
  v->body = 1.;
  v->velocity = clamp(velocity, .02, 1.);
  v->noise = (++serial_ * 747796405u) + 2891336453u;
  const auto length = .6 + clamp(s.decay, 0., 1.) * 1.2;
  if (drum == Drum::kick) {
    v->freq = 43. + s.tone * 22.;
    v->pitch = 110. + s.tone * 50.;
    v->decay = std::exp(-1. / (.14 * length * sr_));
    v->bodyDecay = std::exp(-1. / (.012 * sr_));
  } else if (drum == Drum::snare) {
    v->filterCoeff = coeff(1100., sr_);
    v->freq = 155. + s.tone * 90.;
    v->decay = std::exp(-1. / (.065 * length * sr_));
    v->bodyDecay = std::exp(-1. / (.035 * sr_));
  } else {
    v->filterCoeff = coeff(7000., sr_);
    v->freq = 5200. + s.tone * 3800.;
    v->decay = std::exp(-1. / (.017 * length * sr_));
    v->bodyDecay = std::exp(-1. / (.004 * sr_));
  }
}
std::array<double, 2> Kit::process(const Settings &s) noexcept {
  double l = 0., r = 0.;
  for (auto &v : voices_) {
    if (v.env < 1e-5)
      continue;
    v.noise ^= v.noise << 13;
    v.noise ^= v.noise >> 17;
    v.noise ^= v.noise << 5;
    const double noise = double(v.noise) / 2147483648. - 1.;
    double sample = 0., pan = 0.;
    if (v.drum == Drum::kick) {
      v.phase += 2. * pi * (v.freq + v.pitch * v.body) / sr_;
      sample = (std::sin(v.phase) * .95 + noise * v.body * .11) * v.env;
    } else if (v.drum == Drum::snare) {
      v.phase += 2. * pi * v.freq / sr_;
      v.phase2 += 2. * pi * v.freq * 1.72 / sr_;
      v.filter += v.filterCoeff * (noise - v.filter);
      sample = (noise - v.filter) * v.env * .72 +
               (std::sin(v.phase) + .42 * std::sin(v.phase2)) * v.body * .38;
      pan = -.08;
    } else {
      v.phase += 2. * pi * v.freq / sr_;
      v.phase2 += 2. * pi * v.freq * .731 / sr_;
      v.filter += v.filterCoeff * (noise - v.filter);
      sample = ((noise - v.filter) * 1.35 +
                .12 * std::sin(v.phase) * std::sin(v.phase2)) *
               v.env * .5;
      pan = .16;
    }
    // Bounded phases also avoid loss of precision in very long sessions.
    if (v.phase > 2. * pi)
      v.phase -= 2. * pi;
    if (v.phase2 > 2. * pi)
      v.phase2 -= 2. * pi;
    sample *= v.velocity;
    l += sample * (1. - pan);
    r += sample * (1. + pan);
    v.env *= v.decay;
    v.body *= v.bodyDecay;
  }
  dcL_ += .0008 * (l - dcL_);
  dcR_ += .0008 * (r - dcR_);
  const auto gain = 1. + clamp(s.drive, 0., 1.) * 2.5;
  return {.98 * std::tanh((l - dcL_) * gain),
          .98 * std::tanh((r - dcR_) * gain)};
}
void Engine::prepare(double sr) noexcept {
  sr_ = std::isfinite(sr) ? clamp(sr, 8000., 384000.) : 48000.;
  detector_.prepare(sr_);
  kit_.prepare(sr_);
  clockValid_ = scheduleValid_ = false;
  if (recording_ || tailOpen_)
    cancel();
  if (waitingForBoundary_)
    captureStart_ = std::numeric_limits<double>::quiet_NaN();
}
void Engine::learn(int drum) noexcept {
  if (drum < 0 || drum > 2)
    return;
  cancel();
  learning_ = drum;
  profile_.count[drum] = 0;
  profile_.mean[drum] = {};
  ++revision_;
}
void Engine::arm() noexcept {
  learning_ = -1;
  if (waitingForBoundary_ || recording_ || tailOpen_) {
    cancel();
    return;
  }
  if (!clock_.fourFour) {
    state_ = State::unsupportedMeter;
    return;
  }
  waitingForBoundary_ = true;
  state_ = State::armed;
  captureStart_ = clock_.running ? (std::floor(clock_.beat / 4.) + 1.) * 4.
                                 : std::numeric_limits<double>::quiet_NaN();
}
void Engine::cancel() noexcept {
  learning_ = -1;
  waitingForBoundary_ = recording_ = tailOpen_ = committed_ = false;
  take_.count = 0;
  state_ = active_.count ? State::playing : State::live;
}
void Engine::saveUndo() noexcept {
  history_[historyHead_] = active_;
  historyHead_ = (historyHead_ + 1) % undoDepth;
  historyCount_ = std::min(historyCount_ + 1, undoDepth);
}
void Engine::undo() noexcept {
  if (learning_ >= 0) {
    learning_ = -1;
    return;
  }
  if (waitingForBoundary_ || recording_ || (tailOpen_ && !committed_)) {
    cancel();
    return;
  }
  if (tailOpen_)
    cancel();
  if (!historyCount_)
    return;
  historyHead_ = (historyHead_ + undoDepth - 1) % undoDepth;
  active_ = history_[historyHead_];
  --historyCount_;
  ++revision_;
  resetSchedule();
  state_ = active_.count ? State::playing : State::live;
}
void Engine::clear() noexcept {
  cancel();
  if (active_.count) {
    saveUndo();
    active_.count = 0;
    ++revision_;
    resetSchedule();
    state_ = State::live;
  }
}
void Engine::restore(const Loop &loop) noexcept {
  if (loop.count > maxEvents || !std::isfinite(loop.anchor))
    return;
  Loop checked;
  checked.anchor = std::isfinite(loop.anchor) ? loop.anchor : 0.;
  for (std::size_t i = 0; i < std::min(loop.count, maxEvents); ++i)
    if (!checked.append(loop.events[i]))
      return;
  cancel();
  saveUndo();
  active_ = checked;
  ++revision_;
  resetSchedule();
  state_ = active_.count ? State::playing : State::live;
}
void Engine::tap(Drum drum, float velocity) noexcept {
  hit(drum, velocity, clock_.beat);
}
void Engine::hit(Drum drum, float velocity, double beat) noexcept {
  if (int(drum) < 0 || int(drum) > 2 || !std::isfinite(velocity) ||
      !std::isfinite(beat))
    return;
  lastDrum_ = int(drum);
  ++hitSerial_;
  if (settings_.monitor)
    kit_.trigger(drum, velocity, settings_);
  if ((recording_ || tailOpen_) && beat >= captureStart_ &&
      beat < captureStart_ + loopBeats) {
    Event e{beat - captureStart_, velocity, drum};
    if (!take_.append(e)) {
      // Never replace a good loop with a silently truncated take.
      if (committed_ && historyCount_) {
        historyHead_ = (historyHead_ + undoDepth - 1) % undoDepth;
        active_ = history_[historyHead_];
        --historyCount_;
        ++revision_;
      }
      cancel();
      state_ = State::full;
      resetSchedule();
      return;
    }
    ++takeAdded_;
    if (committed_) {
      active_ = take_;
      ++revision_;
      resetSchedule();
    }
  }
}
void Engine::commit() noexcept {
  if (!takeAdded_) {
    state_ = State::empty;
    return;
  }
  saveUndo();
  active_ = take_;
  committed_ = true;
  ++revision_;
  resetSchedule();
  state_ = State::playing;
}
void Engine::schedule(double beat) noexcept {
  if (!active_.count)
    return;
  if (!scheduleValid_) {
    scheduleCycle_ = std::floor((beat - active_.anchor) / loopBeats);
    nextEvent_ = 0;
    const double local = beat - active_.anchor - scheduleCycle_ * loopBeats;
    while (nextEvent_ < active_.count &&
           active_.events[nextEvent_].beat < local - 1e-8)
      ++nextEvent_;
    if (nextEvent_ == active_.count) {
      nextEvent_ = 0;
      ++scheduleCycle_;
    }
    scheduleValid_ = true;
  }
  // The cursor makes idle samples O(1), regardless of loop density.
  for (size_t n = 0; n < active_.count; ++n) {
    const auto &event = active_.events[nextEvent_];
    if (active_.anchor + scheduleCycle_ * loopBeats + event.beat > beat + 1e-10)
      break;
    kit_.trigger(event.drum, event.velocity, settings_);
    ++playbackSerial_;
    lastPlaybackBeat_ = beat;
    if (++nextEvent_ == active_.count) {
      nextEvent_ = 0;
      ++scheduleCycle_;
    }
  }
}
double Engine::position() const noexcept {
  return phase(clock_.beat -
               ((recording_ || tailOpen_) ? captureStart_ : active_.anchor)) /
         loopBeats;
}
void Engine::process(double input, Clock clock, double &left,
                     double &right) noexcept {
  if (!std::isfinite(input))
    input = 0.;
  if (!std::isfinite(clock.beat)) {
    clock.beat = 0.;
    clock.running = false;
  }
  const bool discontinuity =
      clockValid_ && clock.running && clock_.running &&
      (clock.beat < previousBeat_ - 1e-7 || clock.beat - previousBeat_ > .1);
  const bool stopped = clockValid_ && clock_.running && !clock.running;
  clock_ = clock;
  if (discontinuity || stopped || !clock.fourFour) {
    if (recording_ || tailOpen_) {
      cancel();
      state_ = clock.fourFour ? State::interrupted : State::unsupportedMeter;
    }
    if (waitingForBoundary_ && !clock.fourFour) {
      cancel();
      state_ = State::unsupportedMeter;
    }
    if (waitingForBoundary_)
      captureStart_ = std::numeric_limits<double>::quiet_NaN();
    resetSchedule();
  }
  if (waitingForBoundary_ && clock.running && clock.fourFour) {
    if (!std::isfinite(captureStart_))
      captureStart_ = std::ceil((clock.beat - 1e-8) / 4.) * 4.;
    if (clock.beat >= captureStart_) {
      waitingForBoundary_ = false;
      recording_ = true;
      committed_ = false;
      state_ = State::recording;
      take_ = Loop{};
      take_.anchor = captureStart_;
      takeAdded_ = 0;
      if (settings_.mode == Mode::overdub) {
        for (size_t i = 0; i < active_.count; ++i) {
          auto e = active_.events[i];
          e.beat = phase(active_.anchor + e.beat - captureStart_);
          take_.append(e);
        }
      }
    }
  }
  if (recording_ && clock.beat >= captureStart_ + loopBeats) {
    recording_ = false;
    tailOpen_ = true;
    tailSamples_ = int(.012 * sr_);
    if (takeAdded_)
      commit();
  }
  inputSmooth_ += (settings_.inputGain - inputSmooth_) * .002;
  outputSmooth_ += (settings_.outputGain - outputSmooth_) * .002;
  Detection detection{};
  if (detector_.process(clamp(input * inputSmooth_, -4., 4.), clock.beat,
                        settings_.threshold, detection)) {
    if (learning_ >= 0) {
      detection.drum = Drum(learning_);
      profile_.add(detection.drum, detection.features);
      ++revision_;
      if (profile_.count[learning_] >= 3)
        learning_ = -1;
    } else if (settings_.voice >= 0 && settings_.voice <= 2)
      detection.drum = Drum(settings_.voice);
    else
      detection.drum = profile_.classify(detection);
    hit(detection.drum, detection.velocity, detection.beat);
  }
  if (tailOpen_ && --tailSamples_ <= 0) {
    if (!committed_)
      commit();
    tailOpen_ = false;
    committed_ = false;
  }
  if (clock.running && settings_.playback)
    schedule(clock.beat);
  else
    resetSchedule();
  const auto sample = kit_.process(settings_);
  left = sample[0] * outputSmooth_;
  right = sample[1] * outputSmooth_;
  previousBeat_ = clock.beat;
  clockValid_ = true;
}
} // namespace pocket
