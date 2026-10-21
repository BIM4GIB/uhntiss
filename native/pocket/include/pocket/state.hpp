#pragma once
#include "pocket/engine.hpp"
#include <cmath>

namespace pocket {
// Used only at the control boundary. Versions 2 and 3 retain the same loop
// representation. V2's four-feature centroids cannot classify V3 attacks.
constexpr size_t profileValues = 3 * (1 + 3 * Features{}.size());
constexpr size_t maxStateValues = 3 + maxEvents * 3 + profileValues;
using StateValues = std::array<double, maxStateValues>;
inline size_t encodeState(const Loop &loop, const Profile &profile,
                          StateValues &out) noexcept {
  size_t n = 0;
  out[n++] = 3.;
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
  if (!values || count < 3 || !stateInteger(values[0], 2, 3) ||
      !std::isfinite(values[1]) || !stateInteger(values[2], 0, int(maxEvents)))
    return false;
  const bool legacy = values[0] == 2.;
  const size_t events = size_t(values[2]);
  if (count != 3 + events * 3 + (legacy ? 15 : profileValues))
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
    if (!legacy)
      voice.count[d] = int(values[n]);
    ++n;
    for (size_t i = 0; i < (legacy ? 4 : 3 * Features{}.size()); ++i) {
      const double f = values[n++];
      if (!std::isfinite(f) || f < (legacy ? 0. : -64.) ||
          f > (legacy ? 1. : 64.))
        return false;
      if (!legacy)
        voice.examples[d][i / Features{}.size()][i % Features{}.size()] = f;
    }
  }
  loop = next;
  profile = voice;
  return true;
}
} // namespace pocket
