#pragma once
#include "pocket/engine.hpp"
#include <cmath>

namespace pocket {
// All versions retain loop timing. Older feature profiles are reset because
// their descriptors cannot classify the current causal attacks.
constexpr size_t profileValues = 3 * (1 + 3 * Features{}.size());
constexpr size_t maxStateValues = 3 + maxEvents * 3 + profileValues;
using StateValues = std::array<double, maxStateValues>;
inline size_t encodeState(const Loop &loop, const Profile &profile,
                          StateValues &out) noexcept {
  size_t n = 0;
  out[n++] = 4.;
  out[n++] = loop.anchor;
  out[n++] = double(loop.count);
  for (size_t i = 0; i < loop.count; ++i) {
    out[n++] = loop.events[i].beat;
    out[n++] = loop.events[i].velocity;
    out[n++] = int(loop.events[i].drum);
  }
  for (int d = 0; d < 3; ++d) {
    out[n++] = profile.count[d];
    for (const auto &example : profile.examples[d])
      for (double f : example)
        out[n++] = f;
  }
  return n;
}
inline bool stateInteger(double v, int lo, int hi) noexcept {
  return std::isfinite(v) && v >= lo && v <= hi && v == std::floor(v);
}
inline bool decodeState(const double *values, size_t count, Loop &loop,
                        Profile &profile) noexcept {
  if (!values || count < 3 || !stateInteger(values[0], 2, 4) ||
      !std::isfinite(values[1]) || !stateInteger(values[2], 0, int(maxEvents)))
    return false;
  const bool legacy = values[0] == 2.;
  const bool current = values[0] == 4.;
  const size_t events = size_t(values[2]);
  if (count != 3 + events * 3 + (legacy ? 15 : current ? profileValues : 129))
    return false;
  Loop next;
  Profile voice;
  next.anchor = values[1];
  size_t n = 3;
  for (size_t i = 0; i < events; ++i) {
    const double beat = values[n++], velocity = values[n++], drum = values[n++];
    if (!stateInteger(drum, 0, 2) || !std::isfinite(velocity) ||
        velocity < double(.02f) || velocity > 1. ||
        !next.append({beat, float(velocity), Drum(int(drum))}))
      return false;
  }
  for (int d = 0; d < 3; ++d) {
    if (!stateInteger(values[n], 0, legacy ? 100 : 3))
      return false;
    if (current)
      voice.count[d] = int(values[n]);
    ++n;
    for (size_t i = 0; i < (legacy    ? 4
                            : current ? 3 * Features{}.size()
                                      : 42);
         ++i) {
      const double f = values[n++];
      if (!std::isfinite(f) || f < (legacy ? 0. : -64.) ||
          f > (legacy ? 1. : 64.))
        return false;
      if (current)
        voice.examples[d][i / Features{}.size()][i % Features{}.size()] = f;
    }
  }
  loop = next;
  profile = voice;
  return true;
}
// A profile-only payload: the v4 profile block alone (count + 3 x 17 features
// per voice, profileValues doubles). Lets a host deliver a Teach profile
// built offline (e.g. from one-class enrollment clips) without touching the
// loop. Same validation as decodeState's current branch.
inline bool decodeProfile(const double *values, size_t count,
                          Profile &profile) noexcept {
  if (!values || count != profileValues)
    return false;
  StateValues scratch{};
  scratch[0] = 4.;
  scratch[1] = 0.;
  scratch[2] = 0.;
  for (size_t i = 0; i < profileValues; ++i)
    scratch[3 + i] = values[i];
  Loop loop;
  return decodeState(scratch.data(), 3 + profileValues, loop, profile);
}
} // namespace pocket
