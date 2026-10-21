#include "pocket/engine.hpp"
#include <chrono>
#include <cmath>
#include <iostream>
int main() {
  pocket::Engine e;
  e.prepare(48000.);
  pocket::Loop loop;
  for (int i = 0; i < 512; ++i)
    loop.append({i / 64., .5f, pocket::Drum(i % 3)});
  e.restore(loop);
  const auto begin = std::chrono::steady_clock::now();
  double peak = 0.;
  for (int block = 0; block < 7500; ++block) {
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 64; ++i) {
      double l, r;
      const double t = (block * 64. + i) / 48000.;
      const double within = std::fmod(t, .06);
      const double input =
          within < .025 ? .7 * std::sin(within * 2. * 3.141592653589793 * 95.) *
                              std::exp(-within * 60.)
                        : 0.;
      e.process(input, {t * 2., true, true}, l, r);
    }
    peak = std::max(peak, std::chrono::duration<double>(
                              std::chrono::steady_clock::now() - start)
                              .count());
  }
  const double seconds =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - begin)
          .count();
  std::cout << "10 s audio, 512 loop events + live Auto: " << seconds
            << " s CPU wall time (" << seconds * 10
            << "% of real time); worst observed 64-sample block " << peak * 1000
            << " ms; " << e.hitSerial() << " input attacks classified\n";
}
