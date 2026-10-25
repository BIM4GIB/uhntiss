#pragma once
#include "pocket/engine.hpp"
#include <cmath>

namespace pocket {
// All versions retain loop timing. Older feature profiles are reset because
// their descriptors cannot classify the current causal attacks.
constexpr size_t profileValues = 3 * (1 + 3 * Features{}.size());
constexpr size_t voiceValues = 30 + maxExamples * (17 + 30);
constexpr size_t maxProfileValues = 5 + 240 + maxVoices * voiceValues + maxRejects * 17;
constexpr size_t maxStateValues = 3 + maxEvents * 5 + maxProfileValues;
using StateValues = std::array<double, maxStateValues>;
inline bool stateInteger(double v, int lo, int hi) noexcept {
  return std::isfinite(v) && v >= lo && v <= hi && v == std::floor(v);
}
inline bool decodeLegacyState(const double *values, size_t count, Loop &loop,
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
inline bool decodeLegacyProfile(const double *values, size_t count,
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
  return decodeLegacyState(scratch.data(), 3 + profileValues, loop, profile);
}
// v5 profile: version, feature version, voices, rejects, sample rate,
// 120 scales; each voice: note, 24 ASCII name bytes, early/late counts,
// radius, reserved(2), 3x17 early + 3x120 late; then reject examples.
inline size_t encodeProfile(const Profile &p, double *out) noexcept {
  size_t n = 0;
  out[n++] = 5.; out[n++] = featureVersion; out[n++] = p.voices;
  out[n++] = p.rejects; out[n++] = p.sampleRate;
  for (auto f : p.scale) out[n++] = f;
  for (auto f : p.center) out[n++] = f;
  for (int d = 0; d < p.voices; ++d) {
    out[n++] = p.notes[d];
    for (int j = 0; j < 24; ++j) out[n++] = p.names[d][j];
    out[n++] = p.count[d]; out[n++] = p.lateCount[d]; out[n++] = p.radius[d];
    out[n++] = 0.; out[n++] = 0.;
    for (const auto &e : p.examples[d]) for (auto f : e) out[n++] = f;
    for (const auto &e : p.late[d]) for (int j = 0; j < 120; j += 4) {
      std::uint32_t packed = 0;
      for (int k = 0; k < 4; ++k) packed |= std::uint32_t(std::uint8_t(e[j + k])) << (k * 8);
      out[n++] = double(packed);
    }
  }
  for (int i = 0; i < p.rejects; ++i) for (auto f : p.negative[i]) out[n++] = f;
  return n;
}
inline bool decodeProfile(const double *v, size_t count, Profile &profile) noexcept {
  if (count == profileValues) return decodeLegacyProfile(v, count, profile);
  if (!v || count < 245 || v[0] != 5. || v[1] != featureVersion ||
      !stateInteger(v[2], 2, maxVoices) || !stateInteger(v[3], 0, maxRejects) ||
      !std::isfinite(v[4]) || (v[4] != 0. && (v[4] < 8000. || v[4] > 384000.))) return false;
  const int voices = int(v[2]), rejects = int(v[3]);
  if (count != 245 + voices * voiceValues + rejects * 17) return false;
  Profile p;
  p.voices = voices; p.rejects = rejects; p.sampleRate = v[4];
  size_t n = 5;
  for (auto &f : p.scale) {
    f = v[n++];
    if (!std::isfinite(f) || f < 0. || f > 1e6 || (p.sampleRate && f < 1e-6)) return false;
  }
  for (auto &f : p.center) { f = v[n++]; if (!std::isfinite(f) || std::abs(f) > 1024.) return false; }
  for (int d = 0; d < voices; ++d) {
    if (!stateInteger(v[n], 0, 127)) return false;
    p.notes[d] = int(v[n++]);
    bool ended = false;
    for (int j = 0; j < 24; ++j) {
      if (!stateInteger(v[n], 0, 127)) return false;
      const char c = char(v[n++]);
      if ((!j && (c < 'a' || c > 'z')) || (c && ended) ||
          (c && !((c >= 'a' && c <= 'z') || (j && c >= '0' && c <= '9') || (j && c == '_')))) return false;
      p.names[d][j] = c; ended = ended || !c;
    }
    p.names[d][24] = 0;
    for (int i = 0; i < d; ++i) if (p.names[i] == p.names[d]) return false;
    if (!stateInteger(v[n], 0, maxExamples) || !stateInteger(v[n + 1], 0, maxExamples)) return false;
    p.count[d] = int(v[n++]); p.lateCount[d] = int(v[n++]);
    p.radius[d] = v[n++];
    if (!std::isfinite(p.radius[d]) || p.radius[d] < 0. || p.radius[d] > 1e8 || v[n++] != 0. || v[n++] != 0.) return false;
    for (auto &e : p.examples[d]) for (auto &f : e) {
      f = v[n++]; if (!std::isfinite(f) || std::abs(f) > 64.) return false;
    }
    for (auto &e : p.late[d]) for (int j = 0; j < 120; j += 4) {
      const double value = v[n++];
      if (!std::isfinite(value) || value < 0. || value > 4294967295. || value != std::floor(value)) return false;
      const auto packed = std::uint32_t(value);
      for (int k = 0; k < 4; ++k) {
        const auto byte = int((packed >> (k * 8)) & 255);
        e[j + k] = std::int8_t(byte < 128 ? byte : byte - 256);
      }
    }
  }
  for (int i = 0; i < rejects; ++i) for (auto &f : p.negative[i]) {
    f = v[n++]; if (!std::isfinite(f) || std::abs(f) > 64.) return false;
  }
  profile = p;
  return true;
}
inline size_t encodeState(const Loop &loop, const Profile &profile, StateValues &out) noexcept {
  size_t n = 0;
  out[n++] = 5.; out[n++] = loop.anchor; out[n++] = double(loop.count);
  for (size_t i = 0; i < loop.count; ++i) {
    const auto &e = loop.events[i];
    out[n++] = e.beat; out[n++] = e.velocity; out[n++] = int(e.drum);
    out[n++] = e.voice; out[n++] = e.note;
  }
  n += encodeProfile(profile, out.data() + n);
  return n;
}
inline bool decodeState(const double *v, size_t count, Loop &loop, Profile &profile) noexcept {
  if (!v || count < 3) return false;
  if (v[0] != 5.) return decodeLegacyState(v, count, loop, profile);
  if (!std::isfinite(v[1]) || !stateInteger(v[2], 0, int(maxEvents))) return false;
  const auto events = size_t(v[2]);
  if (count < 3 + events * 5) return false;
  Loop next; next.anchor = v[1];
  Profile p;
  size_t n = 3;
  for (size_t i = 0; i < events; ++i) {
    if (!std::isfinite(v[n + 1]) || v[n + 1] < double(.02f) || v[n + 1] > 1. ||
        !stateInteger(v[n + 2], 0, 3) || !stateInteger(v[n + 3], -1, maxVoices - 1) ||
        !stateInteger(v[n + 4], -1, 127) ||
        !next.append({v[n], float(v[n + 1]), Drum(int(v[n + 2])), int(v[n + 3]), int(v[n + 4])})) return false;
    n += 5;
  }
  if (!decodeProfile(v + n, count - n, p)) return false;
  loop = next; profile = p; return true;
}
} // namespace pocket
