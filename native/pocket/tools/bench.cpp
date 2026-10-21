#include "pocket/engine.hpp"
#include <chrono>
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
      e.process(0., {(block * 64. + i) / 24000., true, true}, l, r);
    }
    peak = std::max(peak, std::chrono::duration<double>(
                              std::chrono::steady_clock::now() - start)
                              .count());
  }
  const double seconds =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - begin)
          .count();
  std::cout << "10 s audio, 512 events: " << seconds << " s CPU wall time ("
            << seconds * 10
            << "% of real time); worst observed 64-sample block " << peak * 1000
            << " ms\n";
}
