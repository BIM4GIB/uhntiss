#include "pocket/engine.hpp"
#include "pocket/attack_model.hpp"
#include "pocket/transient_model.hpp"
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
      int(e.drum) < 0 || int(e.drum) > 3 || e.voice < -1 ||
      e.voice >= maxVoices || e.note < -1 || e.note > 127 ||
      (e.drum == Drum::noise && e.note < 0))
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
namespace {
struct Neighbours {
  std::array<double, 3> distance{1e30, 1e30, 1e30};
  std::array<int, 3> drum{}, source{-1, -1, -1};
  void add(const Features &a, const Features &b, int label,
           int group) noexcept {
    double d = 0.;
    for (size_t j = 0; j < a.size(); ++j) {
      const double delta = (a[j] - b[j]) / model::scale[j];
      d += delta * delta;
    }
    for (size_t i = 0; i < distance.size(); ++i)
      if (source[i] == group) {
        if (d >= distance[i])
          return;
        for (size_t j = i; j + 1 < distance.size(); ++j) {
          distance[j] = distance[j + 1];
          drum[j] = drum[j + 1];
          source[j] = source[j + 1];
        }
        distance.back() = 1e30;
        source.back() = -1;
        break;
      }
    for (size_t i = 0; i < distance.size(); ++i)
      if (d < distance[i]) {
        for (size_t k = distance.size() - 1; k > i; --k) {
          distance[k] = distance[k - 1];
          drum[k] = drum[k - 1];
          source[k] = source[k - 1];
        }
        distance[i] = d;
        drum[i] = label;
        source[i] = group;
        break;
      }
  }
  Drum choose() const noexcept {
    std::array<double, maxVoices> votes{};
    for (size_t i = 0; i < distance.size(); ++i)
      votes[drum[i]] += 1. / (1. + distance[i]);
    return Drum(std::max_element(votes.begin(), votes.end()) - votes.begin());
  }
};
} // namespace
template <class F, class R, class N, class V>
std::array<double, 4> vote(const F &f, const R &roots, const N &nodes,
                           const V &leaves) noexcept {
  std::array<double, 4> result{};
  for (auto root : roots) {
    auto node = &nodes[root];
    while (node->feature >= 0)
      node = &nodes[float(f[node->feature]) <= node->threshold ? node->left
                                                               : node->right];
    for (int d = 0; d < 4; ++d)
      result[d] += leaves[node->left][d];
  }
  return result;
}
Drum classifyAttack(const Features &features, double sampleRate) noexcept {
  // Rate-specific calibration uses the same descriptor and deadline. Exactly
  // one primary forest runs; high-rate IIR descriptors differ slightly.
  const auto first =
      sampleRate > 48000.
          ? vote(features, highrate::roots, highrate::nodes,
                 highrate::leafVotes)
          : vote(features, model::roots, model::nodes, model::leafVotes);
  const int voice =
      int(std::max_element(first.begin(), first.end()) - first.begin());
  if (voice < 3)
    return Drum(voice);
  // Recover only rejected windows. Existing positive voice choices stand.
  const auto second =
      vote(features, recovery::roots, recovery::nodes, recovery::leafVotes);
  const int next = int(std::max_element(second.begin(), second.begin() + 3) -
                       second.begin());
  if (second[3] > .3 || second[next] < .6)
    return Drum::noise;
  return Drum(next);
}
Drum classifyTransientAttack(const TransientFeatures &features) noexcept {
  const auto votes =
      vote(features, transient::roots, transient::nodes, transient::leafVotes);
  const int voice =
      int(std::max_element(votes.begin(), votes.end()) - votes.begin());
  if (voice == 3 || votes[voice] < (voice == 2 ? .8 : .7))
    return Drum::noise;
  return Drum(voice);
}
void Profile::add(Drum drum, const Features &features) noexcept {
  const auto d = int(drum);
  if (d < 0 || d >= voices)
    return;
  for (double f : features)
    if (!std::isfinite(f))
      return;
  const auto i = std::clamp(count[d], 0, 2);
  examples[d][i] = features;
  count[d] = std::min(i + 1, 3);
}
Drum Profile::classify(const Detection &detection) const noexcept {
  const auto voice = classifyVoice(detection);
  return voice < 0 || voice > 2 ? Drum::noise : Drum(voice);
}
int Profile::classifyVoice(const Detection &detection,
                           const Slices *rich) const noexcept {
  if (!ready() || (sampleRate == 0. && detection.drum == Drum::noise))
    return detection.drum == Drum::noise ? -1 : int(detection.drum);
  Neighbours n;
  for (int d = 0; d < voices; ++d)
    for (int i = 0; i < count[d]; ++i)
      n.add(detection.features, examples[d][i], d, d * maxExamples + i);
  const auto choice = int(n.choose());
  // A per-user reject class competes in a separate 5-neighbour vote.
  // Require four negative neighbours: one similar breath must not erase a
  // real ghost hit. Positive examples win exact distance ties.
  std::array<double, 5> nearest{{1e30, 1e30, 1e30, 1e30, 1e30}};
  std::array<bool, 5> isNoise{};
  const auto consider = [&](const Features &features, bool noise) {
    double distance = 0.;
    for (size_t j = 0; j < Features{}.size(); ++j) {
      const double delta =
          (detection.features[j] - features[j]) / model::scale[j];
      distance += delta * delta;
    }
    for (int i = 0; i < 5; ++i)
      if (distance < nearest[i]) {
        for (int k = 4; k > i; --k) {
          nearest[k] = nearest[k - 1];
          isNoise[k] = isNoise[k - 1];
        }
        nearest[i] = distance;
        isNoise[i] = noise;
        break;
      }
  };
  if (rich && delayed() && lateRejects >= 4) {
    std::array<double, 40> normalized{};
    for (int j = 0; j < 40; ++j)
      normalized[j] = (rich->values[j] - center[j]) / scale[j];
    const auto considerRich = [&](const std::array<std::int8_t, 120> &features,
                                  bool noise) {
      double distance = 0.;
      for (int j = 0; j < 40; ++j) {
        const double delta = normalized[j] - double(features[j]) * .0625;
        distance += delta * delta;
      }
      for (int i = 0; i < 5; ++i)
        if (distance < nearest[i]) {
          for (int k = 4; k > i; --k) {
            nearest[k] = nearest[k - 1];
            isNoise[k] = isNoise[k - 1];
          }
          nearest[i] = distance;
          isNoise[i] = noise;
          break;
        }
    };
    for (int d = 0; d < voices; ++d)
      for (int i = 0; i < lateCount[d]; ++i)
        considerRich(late[d][i], false);
    for (int i = 0; i < lateRejects; ++i)
      if (negativeSlices[i] == 7)
        considerRich(negativeLate[i], true);
    if (std::count(isNoise.begin(), isNoise.end(), true) >= 4)
      return -1;
  } else if (rejects >= 4) {
    for (int d = 0; d < voices; ++d)
      for (int i = 0; i < count[d]; ++i)
        consider(examples[d][i], false);
    for (int i = 0; i < rejects; ++i)
      consider(negative[i], true);
    if (std::count(isNoise.begin(), isNoise.end(), true) >= 4)
      return -1;
  }
  double supported = 1e30;
  for (size_t i = 0; i < n.distance.size(); ++i)
    if (n.drum[i] == choice)
      supported = std::min(supported, n.distance[i]);
  if (radius[choice] > 0. && supported > radius[choice])
    return -1;
  return choice;
}
int Profile::classifyLate(const Slices &features,
                          int provisional) const noexcept {
  if (!delayed())
    return provisional;
  std::array<double, 5> distances{{1e30, 1e30, 1e30, 1e30, 1e30}};
  std::array<int, 5> labels{};
  const int expected[] = {int(std::round(.010 * sampleRate)),
                          int(std::round(.030 * sampleRate)),
                          int(std::round(.040 * sampleRate))};
  // A shortened FFT has a different spectral/energy distribution. Compare
  // completed slices only, never a 5 ms tail against a 40 ms enrollment tail.
  if (features.samples[1] < expected[1])
    return provisional;
  const int dimensions = features.samples[2] >= expected[2] ? 120 : 80;
  SliceFeatures normalized{};
  for (int j = 0; j < dimensions; ++j)
    normalized[j] = (features.values[j] - center[j]) / scale[j];
  for (int d = 0; d < voices; ++d)
    for (int e = 0; e < lateCount[d]; ++e) {
      double distance = 0.;
      for (int j = 0; j < dimensions; ++j) {
        const double delta = normalized[j] - double(late[d][e][j]) * .0625;
        distance += delta * delta;
      }
      for (int k = 0; k < 5; ++k)
        if (distance < distances[k]) {
          for (int m = 4; m > k; --m) {
            distances[m] = distances[m - 1];
            labels[m] = labels[m - 1];
          }
          distances[k] = distance;
          labels[k] = d;
          break;
        }
    }
  auto gated = distances;
  std::array<bool, 5> noise{};
  const int required = features.samples[2] >= expected[2] ? 7 : 3;
  for (int i = 0; i < lateRejects; ++i) {
    if ((negativeSlices[i] & required) != required)
      continue;
    double distance = 0.;
    for (int j = 0; j < dimensions; ++j) {
      const double delta = normalized[j] - double(negativeLate[i][j]) * .0625;
      distance += delta * delta;
    }
    for (int k = 0; k < 5; ++k)
      if (distance < gated[k]) {
        for (int m = 4; m > k; --m) {
          gated[m] = gated[m - 1];
          noise[m] = noise[m - 1];
        }
        gated[k] = distance;
        noise[k] = true;
        break;
      }
  }
  if (std::count(noise.begin(), noise.end(), true) >= 4)
    return -1;
  std::array<double, maxVoices> votes{};
  for (int i = 0; i < 5; ++i)
    if (distances[i] < 1e30)
      votes[labels[i]] += 1.;
  return int(std::max_element(votes.begin(), votes.begin() + voices) -
             votes.begin());
}
void AttackTracker::prepare(double sr, bool transient) noexcept {
  sr = std::isfinite(sr) ? clamp(sr, 8000., 384000.) : 48000.;
  sr_ = sr;
  transient_ = transient;
  window_ = int(std::round(model::windowSeconds * sr));
  const double edges[] = {150., 400., 1000., 2500., 6000.};
  for (size_t i = 0; i < bands_.size(); ++i)
    bandCoeffs_[i] = coeff(std::min(edges[i], sr * .45), sr);
  dcCoeff_ = coeff(25., sr);
  onsetCoeff_ = coeff(70., sr);
  historySize_ = int(std::round(.030 * sr));
  attack_ = 1. - std::exp(-1. / (.0004 * sr));
  release_ = 1. - std::exp(-1. / ((transient_ ? .002 : .004) * sr));
  slowCoeff_ = 1. - std::exp(-1. / ((transient_ ? .008 : .045) * sr));
  contextCoeff_ = 1. - std::exp(-1. / (.012 * sr));
  reset();
}
void AttackTracker::reset() noexcept {
  hp_ = dc_ = fast_ = slow_ = previous_ = total_ = peak_ = 0.;
  history_ = {};
  bandFast_ = bandSlow_ = context_ = prior_ = {};
  historySum_ = onsetPower_ = earlyPower_ = onsetDc_ = onsetPeak_ =
      rejectedPeak_ = 0.;
  historyIndex_ = historyCount_ = 0;
  bands_ = {};
  energy_ = {};
  collect_ = refractory_ = count_ = crossings_ = quiet_ = releaseSamples_ = 0;
  released_ = true;
  started_ = false;
}
float AttackTracker::attackVelocity() const noexcept {
  return float(clamp(std::pow(peak_, .55), .08, 1.));
}
bool AttackTracker::process(double x, double beat, double threshold,
                            Detection &result, Policy policy) noexcept {
  const bool recoverRejected = policy != Policy::fixedVoice;
  started_ = false;
  if (!std::isfinite(x))
    x = 0.;
  threshold = std::isfinite(threshold) ? clamp(threshold, .002, .2) : .010;
  if (policy == Policy::quiet)
    threshold *= .3;
  if (transient_ && policy != Policy::transientCandidates)
    threshold *= .3;
  dc_ += dcCoeff_ * (x - dc_);
  hp_ = x - dc_;
  for (size_t i = 0; i < bands_.size(); ++i)
    bands_[i] += bandCoeffs_[i] * (hp_ - bands_[i]);
  onsetDc_ += onsetCoeff_ * (x - onsetDc_);
  const auto a = std::abs(x - onsetDc_);
  fast_ += (a > fast_ ? attack_ : release_) * (a - fast_);
  slow_ += slowCoeff_ * (a - slow_);
  double novelty = 0.;
  if (transient_) {
    // A new frequency band can rise while an earlier, louder drum still
    // dominates the broadband envelope. Context is exclusively past audio.
    double low = 0.;
    for (size_t j = 0; j < 6; ++j) {
      const double band = j < 5 ? bands_[j] : hp_;
      const double value = std::abs(band - low);
      low = band;
      bandFast_[j] +=
          (value > bandFast_[j] ? attack_ : release_) * (value - bandFast_[j]);
      bandSlow_[j] += slowCoeff_ * (value - bandSlow_[j]);
      novelty =
          std::max(novelty, bandFast_[j] / (bandSlow_[j] + threshold * .2));
    }
  }
  // Once the input really falls below the gate, an earlier loud hit must
  // not mask the next quiet attack through the slow envelope's long decay.
  if (fast_ < threshold * .5) {
    quiet_ = std::min(quiet_ + 1, int(.002 * sr_) + 1);
    if (quiet_ > int(.002 * sr_))
      slow_ = std::min(slow_, fast_);
  } else
    quiet_ = 0;
  if (refractory_ > 0)
    --refractory_;
  const int releaseSamples = int((transient_ ? .001 : .004) * sr_);
  if (transient_ ? novelty < 1.1
                 : (fast_ < threshold * .5 || fast_ < slow_ * .65)) {
    releaseSamples_ = std::min(releaseSamples_ + 1, releaseSamples + 1);
    if (releaseSamples_ > releaseSamples)
      released_ = true;
  } else
    releaseSamples_ = 0;
  // A rejected precursor must not consume the next real attack. Require a
  // fresh rise above its peak; a steady tail cannot repeatedly re-arm itself.
  const bool freshAfterReject =
      recoverRejected && rejectedPeak_ > 0. && fast_ > rejectedPeak_ * 1.8;
  const bool onset = transient_ ? novelty > 1.65 && released_
                                : fast_ > threshold && fast_ > slow_ * 1.65 &&
                                      (released_ || freshAfterReject);
  if (!collect_ && !refractory_ && onset) {
    started_ = true;
    collect_ = window_;
    count_ = crossings_ = 0;
    energy_ = {};
    total_ = peak_ = 0.;
    earlyPower_ = 0.;
    onsetPower_ = historyCount_ ? historySum_ / historyCount_ : 0.;
    onsetPeak_ = fast_;
    rejectedPeak_ = 0.;
    onsetBeat_ = beat;
    prior_ = context_;
    released_ = false;
    releaseSamples_ = 0;
    refractory_ = int((transient_ ? .020 : .035) * sr_);
  }
  bool detected = false;
  if (collect_) {
    onsetPeak_ = std::max(onsetPeak_, fast_);
    auto &energies = energy_[count_ < window_ / 2 ? 0 : 1];
    double previousBand = 0.;
    for (size_t i = 0; i < bands_.size(); ++i) {
      double band = bands_[i] - previousBand;
      energies[i] += band * band;
      previousBand = bands_[i];
    }
    energies[5] += (hp_ - previousBand) * (hp_ - previousBand);
    total_ += hp_ * hp_;
    if (count_ < window_ / 2)
      earlyPower_ += hp_ * hp_;
    peak_ = std::max(peak_, std::abs(hp_));
    crossings_ += (hp_ * previous_ < 0.);
    ++count_;
    if (!--collect_) {
      const auto features = descriptor();
      const bool quiet = policy == Policy::quiet;
      auto drum = Drum::noise;
      TransientFeatures transientFeatures{};
      if (transient_) {
        std::copy(features.begin(), features.end(), transientFeatures.begin());
        for (size_t j = 0; j < 6; ++j)
          transientFeatures[17 + j] =
              std::log(((energy_[0][j] + energy_[1][j]) / count_ + 1e-12) /
                       (prior_[j] + 1e-12));
        drum = classifyTransientAttack(transientFeatures);
        if (attackVelocity() > .22f)
          drum = Drum::noise;
      } else if (quiet) {
        const auto votes = vote(features, recovery::roots, recovery::nodes,
                                recovery::leafVotes);
        const int voice = int(
            std::max_element(votes.begin(), votes.begin() + 3) - votes.begin());
        if (voice == 2 && votes[3] <= .4 && attackVelocity() <= .22f)
          drum = Drum::hat;
      } else
        drum = classifyAttack(features, sr_);
      result = {drum, attackVelocity(), onsetBeat_, features};
      if (transient_)
        std::copy(transientFeatures.begin() + 17, transientFeatures.end(),
                  result.bandContrast.begin());
      if (recoverRejected && (transient_ || result.drum == Drum::noise ||
                              policy == Policy::candidates)) {
        refractory_ = 0;
        rejectedPeak_ = onsetPeak_;
      }
      detected = true;
    }
  }
  historySum_ += hp_ * hp_ - history_[historyIndex_];
  history_[historyIndex_] = hp_ * hp_;
  historyIndex_ = (historyIndex_ + 1) % historySize_;
  historyCount_ = std::min(historyCount_ + 1, historySize_);
  if (transient_) {
    double low = 0.;
    for (size_t j = 0; j < 6; ++j) {
      const double band = j < 5 ? bands_[j] : hp_;
      const double value = band - low;
      context_[j] += contextCoeff_ * (value * value - context_[j]);
      low = band;
    }
  }
  previous_ = hp_;
  return detected;
}
Features AttackTracker::descriptor() const noexcept {
  Features features{};
  for (size_t half = 0; half < 2; ++half) {
    double mean = 0.;
    const double samples = half ? window_ - window_ / 2 : window_ / 2;
    for (size_t band = 0; band < 6; ++band) {
      features[half * 6 + band] =
          std::log(std::max(energy_[half][band] / samples, 1e-12));
      mean += features[half * 6 + band] / 6.;
    }
    for (size_t band = 0; band < 6; ++band)
      features[half * 6 + band] -= mean;
  }
  features[12] = double(crossings_) / count_ * sr_ / 48000. * 10.;
  features[13] = std::sqrt(total_ / count_) / (peak_ + 1e-12) * 5.;
  const double power = total_ / count_;
  features[14] =
      std::log((power + 1e-12) / (std::max(0., onsetPower_) + 1e-12));
  features[15] =
      std::log(((total_ - earlyPower_) / (window_ - window_ / 2) + 1e-12) /
               (earlyPower_ / (window_ / 2) + 1e-12));
  features[16] = std::log(power + 1e-12);
  return features;
}
namespace {
// Fault read-only model pages in during DSP setup, never on the first hit.
// Touch every page, rather than just one synthetic path through each forest.
template <class T> void resident(const T &table) noexcept {
  const volatile auto *bytes =
      reinterpret_cast<const volatile unsigned char *>(&table);
  for (size_t i = 0; i < sizeof(table); i += 1024)
    (void)bytes[i];
  (void)bytes[sizeof(table) - 1];
}
void prepareModels() noexcept {
  resident(model::nodes);
  resident(model::leafVotes);
  resident(model::roots);
  resident(highrate::nodes);
  resident(highrate::leafVotes);
  resident(highrate::roots);
  resident(recovery::nodes);
  resident(recovery::leafVotes);
  resident(recovery::roots);
  resident(transient::nodes);
  resident(transient::leafVotes);
  resident(transient::roots);
  // Resolve the mathematical and tree-walking code before entering DSP too.
  volatile auto early = classifyAttack(Features{}, 48000.);
  volatile auto high = classifyAttack(Features{}, 96000.);
  volatile auto quiet = classifyTransientAttack(TransientFeatures{});
  (void)early;
  (void)high;
  (void)quiet;
}
} // namespace
void Detector::prepare(double sr) noexcept {
  prepareModels();
  sr = std::isfinite(sr) ? clamp(sr, 8000., 384000.) : 48000.;
  primary_.prepare(sr);
  quiet_.prepare(sr);
  transient_.prepare(sr, true);
  // This recovery model is validated at 48 kHz. Other rates keep their
  // existing detector path; there is no hidden resampling or added buffer.
  transientEnabled_ = sr == 48000.;
  refractory_ = int(.035 * sr);
  window_ = int(std::round(model::windowSeconds * sr));
  reset();
}
void Detector::reset() noexcept {
  primary_.reset();
  quiet_.reset();
  transient_.reset();
  sample_ = 0;
  lastAccepted_ = -1000000;
  previousPolicy_ = Policy::automatic;
}
bool Detector::process(double input, double beat, double threshold,
                       Detection &result, Policy policy) noexcept {
  if (policy != previousPolicy_) {
    quiet_.reset();
    transient_.reset();
    previousPolicy_ = policy;
    lastAccepted_ = -1000000;
  }
  ++sample_;
  boundary_ = false;
  if (policy == Policy::transientCandidates)
    return transient_.process(input, beat, threshold, result, policy);
  Detection primary{}, quiet{};
  const bool ready = primary_.process(
      input, beat, threshold, primary,
      policy == Policy::enrolled ? Policy::fixedVoice : policy);
  boundary_ = primary_.started();
  if (policy != Policy::automatic && policy != Policy::enrolled) {
    if (ready)
      result = primary;
    return ready;
  }
  const bool quietReady =
      quiet_.process(input, beat, threshold, quiet, Policy::quiet);
  Detection transient{};
  const bool transientReady =
      transientEnabled_ &&
      transient_.process(input, beat, threshold, transient);
  // Compare sample positions, never beat subdivisions. All deadlines are
  // exactly one window after their own onset; no extra waiting or snapping.
  const bool available = sample_ - window_ + 1 - lastAccepted_ >= refractory_;
  boundary_ =
      boundary_ ||
      (available && !primary_.pending() &&
       (quiet_.started() || (transientEnabled_ && transient_.started())));
  if (ready && (primary.drum != Drum::noise || policy == Policy::enrolled) &&
      available) {
    result = primary;
    lastAccepted_ = sample_ - window_ + 1;
    return true;
  }
  // An active primary attack wins. Breath/tails must pass both the quiet
  // hat model and the raw-velocity cap before they can produce a trigger.
  if (quietReady && quiet.drum != Drum::noise && !primary_.pending() &&
      available) {
    result = quiet;
    lastAccepted_ = sample_ - window_ + 1;
    return true;
  }
  if (transientReady && transient.drum != Drum::noise && !primary_.pending() &&
      available) {
    result = transient;
    lastAccepted_ = sample_ - window_ + 1;
    return true;
  }
  if (ready && policy != Policy::enrolled) {
    result = primary;
    result.drum = Drum::noise;
    return true;
  }
  return false;
}
int Detector::pendingAge() const noexcept {
  int oldest = 0;
  for (const auto *tracker : {&primary_, &quiet_, &transient_}) {
    const auto age = tracker->pendingAge();
    if (age && sample_ - age - lastAccepted_ >= refractory_)
      oldest = std::max(oldest, age);
  }
  return oldest;
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
std::uint64_t Kit::trigger(Drum drum, float velocity,
                           const Settings &s) noexcept {
  if (int(drum) < 0 || int(drum) > 2 || !std::isfinite(velocity))
    return 0;
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
  v->velocity = v->targetVelocity = clamp(velocity, .02, 1.);
  v->id = ++serial_;
  v->noise = (std::uint32_t(v->id) * 747796405u) + 2891336453u;
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
  return v->id;
}
void Kit::refine(std::uint64_t id, float velocity) noexcept {
  if (!id || !std::isfinite(velocity))
    return;
  for (auto &v : voices_)
    if (v.id == id) {
      v.targetVelocity = clamp(velocity, .02, 1.);
      return;
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
    v.velocity += (v.targetVelocity - v.velocity) * .05;
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
  earlyDrum_ = -1;
  earlyVoice_ = 0;
  detector_.prepare(sr_);
  kit_.prepare(sr_);
  slices_.prepare(sr_);
  pending_.active = false;
  midiRead_ = midiWrite_ = 0;
  clockValid_ = scheduleValid_ = false;
  if (recording_ || tailOpen_)
    cancel();
  if (waitingForBoundary_)
    captureStart_ = std::numeric_limits<double>::quiet_NaN();
}
void Engine::learn(int drum) noexcept {
  if (drum < 0 || drum >= profile_.voices)
    return;
  cancel();
  detector_.reset();
  earlyDrum_ = -1;
  earlyVoice_ = 0;
  learning_ = drum;
  profile_.count[drum] = 0;
  profile_.examples[drum] = {};
  profile_.lateCount[drum] = 0;
  profile_.radius[drum] = 0.;
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
  pending_.active = false;
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
bool Engine::popMidi(MidiHit &hit) noexcept {
  if (midiRead_ == midiWrite_)
    return false;
  hit = midi_[midiRead_];
  midiRead_ = (midiRead_ + 1) % int(midi_.size());
  return true;
}
Event Engine::voiceEvent(int voice, float velocity) const noexcept {
  Event e{0., velocity, Drum::noise, voice, profile_.notes[voice]};
  if (e.note == 35 || e.note == 36)
    e.drum = Drum::kick;
  else if (e.note == 38 || e.note == 40)
    e.drum = Drum::snare;
  else if (e.note == 42 || e.note == 44 || e.note == 46)
    e.drum = Drum::hat;
  return e;
}
void Engine::sound(Event e) noexcept {
  if (e.drum != Drum::noise)
    kit_.trigger(e.drum, e.velocity, settings_);
  const int next = (midiWrite_ + 1) % int(midi_.size());
  if (next != midiRead_) {
    const int defaults[] = {36, 38, 42};
    midi_[midiWrite_] = {e.note >= 0 ? e.note : defaults[int(e.drum)],
                         e.velocity};
    midiWrite_ = next;
  }
}
void Engine::hitVoice(int voice, float velocity, double beat,
                      bool audible) noexcept {
  if (voice < 0 || voice >= profile_.voices || !std::isfinite(velocity) ||
      !std::isfinite(beat))
    return;
  lastDrum_ = voice;
  ++hitSerial_;
  const auto e = voiceEvent(voice, velocity);
  if (settings_.monitor && audible)
    sound(e);
  store(e, beat);
}
void Engine::hit(Drum drum, float velocity, double beat,
                 bool audible) noexcept {
  if (int(drum) < 0 || int(drum) > 2 || !std::isfinite(velocity) ||
      !std::isfinite(beat))
    return;
  lastDrum_ = int(drum);
  ++hitSerial_;
  Event e{0., velocity, drum};
  if (settings_.monitor && audible)
    sound(e);
  store(e, beat);
}
void Engine::finishDecision() noexcept {
  if (!pending_.active)
    return;
  const auto decision = pending_;
  pending_.active = false;
  const auto features = slices_.extract(decision.start, decision.end);
  const auto voice = profile_.classifyLate(features, decision.voice);
  if (voice >= 0)
    store(voiceEvent(voice, decision.hit.velocity), decision.hit.beat);
}
void Engine::store(Event e, double beat) noexcept {
  if ((recording_ || tailOpen_) && beat >= captureStart_ &&
      beat < captureStart_ + loopBeats) {
    e.beat = beat - captureStart_;
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
    sound(event);
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
    tailSamples_ = int((profile_.delayed() ? .082 : .012) * sr_);
    if (takeAdded_)
      commit();
  }
  inputSmooth_ += (settings_.inputGain - inputSmooth_) * .002;
  outputSmooth_ += (settings_.outputGain - outputSmooth_) * .002;
  const double raw = clamp(input * inputSmooth_, -4., 4.);
  slices_.push(raw);
  Detection detection{};
  const bool enrolled = profile_.ready() && profile_.sampleRate == sr_;
  const bool complete =
      detector_.process(raw, clock.beat, settings_.threshold, detection,
                        settings_.voice < 0 && learning_ < 0
                            ? (enrolled ? AttackTracker::Policy::enrolled
                                        : AttackTracker::Policy::automatic)
                            : AttackTracker::Policy::fixedVoice);
  if (detector_.started()) {
    earlyDrum_ = learning_ >= 0 ? learning_
                                : (settings_.voice >= 0 && settings_.voice <= 2
                                       ? settings_.voice
                                       : -1);
    earlyVoice_ = 0;
    if (earlyDrum_ >= 0 && settings_.monitor) {
      const Event e =
          learning_ >= 0
              ? voiceEvent(earlyDrum_, detector_.attackVelocity())
              : Event{0., detector_.attackVelocity(), Drum(earlyDrum_)};
      sound(e);
      if (e.drum != Drum::noise)
        earlyVoice_ = kit_.triggerCount();
    }
  }
  // A known voice sounds on the threshold-crossing sample. Refine the same
  // voice's dynamics as its attack unfolds; never make a second transient.
  if (earlyVoice_ && (detector_.pending() || complete))
    kit_.refine(earlyVoice_, detector_.attackVelocity());
  if (complete) {
    if (pending_.active) {
      pending_.end = std::min(pending_.end,
                              slices_.position() - int(std::round(.010 * sr_)));
      finishDecision();
    }
    if (learning_ >= 0) {
      detection.drum = Drum(learning_);
      profile_.add(detection.drum, detection.features);
      ++revision_;
      if (profile_.count[learning_] >= 3)
        learning_ = -1;
      hitVoice(int(detection.drum), detection.velocity, detection.beat, false);
    } else if (earlyDrum_ >= 0) {
      hit(Drum(earlyDrum_), detection.velocity, detection.beat, false);
    } else {
      Slices first;
      const bool richReject =
          enrolled && profile_.delayed() && profile_.lateRejects >= 4;
      if (richReject)
        first =
            slices_.extract(slices_.position() - int(std::round(.010 * sr_)),
                            slices_.position());
      const int voice =
          (profile_.sampleRate == 0. || enrolled)
              ? profile_.classifyVoice(detection, richReject ? &first : nullptr)
              : (detection.drum == Drum::noise ? -1 : int(detection.drum));
      if (voice >= 0) {
        if (enrolled && profile_.delayed()) {
          // Audible deadline is unchanged. Only capture waits for body/tail.
          lastDrum_ = voice;
          ++hitSerial_;
          if (settings_.monitor)
            sound(voiceEvent(voice, detection.velocity));
          if (recording_ || tailOpen_) {
            const auto start = slices_.position() - int(std::round(.010 * sr_));
            if (pending_.active)
              pending_.end = std::min(pending_.end, start);
            finishDecision();
            pending_ = {detection, voice, start,
                        start + int(std::round(.080 * sr_)), true};
          }
        } else if (profile_.ready() && (enrolled || profile_.sampleRate == 0.))
          hitVoice(voice, detection.velocity, detection.beat);
        else
          hit(Drum(voice), detection.velocity, detection.beat);
      }
    }
    earlyDrum_ = -1;
    earlyVoice_ = 0;
  }
  if (pending_.active && slices_.position() >= pending_.end) {
    // At the deadline, an unresolved attack is conservatively excluded.
    // Already-rejected helper candidates do not chop a drum's own tail.
    const int age = detector_.pendingAge();
    const auto boundary = slices_.position() - age;
    if (age && boundary > pending_.start)
      pending_.end = std::min(pending_.end, boundary);
    finishDecision();
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
