#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <cstdint>

namespace pocket {
using SliceFeatures = std::array<double, 120>;
struct Slices {
  SliceFeatures values{};
  // Samples observed in each slice; absent slices never enter the metric.
  std::array<int, 3> samples{};
};
// Fixed memory, no allocation, locks or future reads. Shared by export and DSP.
class SliceExtractor {
public:
  static constexpr int ringSize = 65536, fftSize = 16384;
  void prepare(double sr) noexcept {
    sr_ = sr;
    reset();
    for (int i = 0; i < fftSize / 2; ++i)
      roots_[i] = std::polar(1., -2. * pi * i / fftSize);
    const double lo = hzToMel(60.), hi = hzToMel(sr / 2.);
    for (int i = 0; i < 26; ++i)
      edges_[i] = melToHz(lo + (hi - lo) * i / 25.);
    for (int i = 0; i < 13; ++i)
      for (int j = 0; j < 24; ++j)
        dct_[i][j] = std::cos(pi * i * (2 * j + 1) / 48.) *
                     std::sqrt((i ? 2. : 1.) / 24.);
  }
  void reset() noexcept { ring_.fill(0.); position_ = 0; }
  std::int64_t position() const noexcept { return position_; }
  void push(double sample) noexcept {
    ring_[position_ % ringSize] = sample;
    ++position_;
  }
  Slices extract(std::int64_t onset, std::int64_t end) noexcept {
    Slices out;
    const int edges[] = {0, int(std::round(.010 * sr_)),
                         int(std::round(.040 * sr_)), int(std::round(.080 * sr_))};
    end = std::min(end, position_);
    for (int part = 0; part < 3; ++part) {
      const auto start = onset + edges[part];
      const int n = int(std::max<std::int64_t>(0, std::min(onset + edges[part + 1], end) - start));
      // No padding across the following attack. Too-short slices are absent.
      if (n < 32) continue;
      out.samples[part] = n;
      window(start, n, part == 0 ? int(std::round(.030 * sr_)) : 0,
             out.values.data() + part * 40);
    }
    return out;
  }
private:
  static constexpr double pi = 3.14159265358979323846;
  static double hzToMel(double hz) noexcept {
    return hz < 1000. ? hz / (200. / 3.) : 15. + std::log(hz / 1000.) / (std::log(6.4) / 27.);
  }
  static double melToHz(double mel) noexcept {
    return mel < 15. ? mel * (200. / 3.) : 1000. * std::exp((mel - 15.) * (std::log(6.4) / 27.));
  }
  double at(std::int64_t i) const noexcept {
    return i < 0 || i >= position_ || i < position_ - ringSize ? 0. : ring_[i % ringSize];
  }
  void window(std::int64_t start, int n, int pre, double *out) noexcept {
    int fft = 256;
    while (fft < n) fft *= 2;
    if (fft > fftSize) return;
    std::fill(work_.begin(), work_.begin() + fft, std::complex<double>{});
    const double step = 2. * pi / (n - 1), cs = std::cos(step), sn = std::sin(step);
    double c = 1., s = 0., energy = 0., hsum = 0.;
    int crossings = 0, prev = 0;
    for (int i = 0; i < n; ++i) {
      const double h = (i == 0 || i == n - 1) ? 0. : .5 - .5 * c;
      const double x = at(start + i) * h;
      work_[i] = x;
      const int sign = (x > 0.) - (x < 0.);
      if (i && sign != prev) ++crossings;
      prev = sign; energy += x * x; hsum += h;
      const double next = c * cs - s * sn;
      s = s * cs + c * sn; c = next;
    }
    for (int i = 1, j = 0; i < fft; ++i) {
      int bit = fft >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) std::swap(work_[i], work_[j]);
    }
    for (int length = 2; length <= fft; length *= 2)
      for (int base = 0; base < fft; base += length)
        for (int j = 0; j < length / 2; ++j) {
          const auto a = work_[base + j];
          const auto b = work_[base + j + length / 2] * roots_[j * fftSize / length];
          work_[base + j] = a + b; work_[base + j + length / 2] = a - b;
        }
    std::array<double, 24> mel{};
    double sum = 0., centroid = 0.;
    for (int i = 0; i <= fft / 2; ++i) {
      const double power = std::norm(work_[i]) + 1e-12, hz = i * sr_ / fft;
      sum += power; centroid += hz * power;
      for (int m = 0; m < 24; ++m) {
        const double weight = std::max(0., std::min((hz - edges_[m]) / (edges_[m + 1] - edges_[m]),
                                                   (edges_[m + 2] - hz) / (edges_[m + 2] - edges_[m + 1]))) *
                              2. / (edges_[m + 2] - edges_[m]);
        // librosa's Slaney filterbank is float32, including its normalization.
        mel[m] += power * double(float(weight));
      }
    }
    double msum = 0.;
    for (auto &v : mel) { v += 1e-12; msum += v; }
    for (int m = 0; m < 24; ++m) out[m] = std::log(mel[m] / msum);
    for (int i = 0; i < 13; ++i) {
      out[24 + i] = 0.;
      for (int m = 0; m < 24; ++m) out[24 + i] += dct_[i][m] * std::log(mel[m]);
    }
    double before = 0.;
    const auto first = std::max<std::int64_t>(0, start - pre);
    for (auto i = first; i < start; ++i) before += at(i) * at(i);
    if (start > first) before /= start - first;
    out[37] = double(crossings) / (n - 1);
    out[38] = centroid / sum / (sr_ / 2.);
    out[39] = std::log((energy / n / std::pow(hsum / n, 2.) + 1e-9) / (before + 1e-9));
  }
  double sr_ = 48000.;
  std::int64_t position_ = 0;
  std::array<double, ringSize> ring_{};
  std::array<std::complex<double>, fftSize> work_{};
  std::array<std::complex<double>, fftSize / 2> roots_{};
  std::array<double, 26> edges_{};
  std::array<std::array<double, 24>, 13> dct_{};
};
} // namespace pocket
