// Headless integration/audition: stdin mono f32; argv: rate, bpm, output f32.
// Runs the same Engine as Max, captures two bars, then plays the captured loop.
#include "pocket/engine.hpp"
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
int main(int argc, char **argv) {
  if (argc != 4 && argc != 5)
    return 1;
  const double sr = std::atof(argv[1]), bpm = std::atof(argv[2]);
  const double startBeat = argc == 5 ? std::atof(argv[4]) : 0.;
  if (!std::isfinite(sr) || !std::isfinite(bpm) || sr < 8000 || sr > 192000 ||
      bpm < 40 || bpm > 300 || !std::isfinite(startBeat) || startBeat < 0.)
    return 1;
  std::ofstream out(argv[3], std::ios::binary);
  if (!out)
    return 1;
  pocket::Engine e;
  e.prepare(sr);
  bool armed = false;
  uint64_t i = 0;
  float x;
  auto sample = [&](double input) {
    if (!armed && i * bpm / (60. * sr) >= startBeat) {
      e.arm();
      armed = true;
    }
    double l, r;
    e.process(input, {i * bpm / (60. * sr), true, true}, l, r);
    const float audio[] = {float(l), float(r)};
    out.write(reinterpret_cast<const char *>(audio), sizeof(audio));
    ++i;
  };
  while (std::cin.read(reinterpret_cast<char *>(&x), sizeof(x)))
    sample(x);
  // Finish the capture tail, then one complete repeat, without changing clock.
  while (i < uint64_t(std::ceil(((startBeat + 16.) * 60. / bpm + .05) * sr)))
    sample(0.);
  std::cout << std::setprecision(12);
  for (size_t j = 0; j < e.loop().count; ++j) {
    auto v = e.loop().events[j];
    std::cout << v.beat << ',' << int(v.drum) << ',' << v.velocity << '\n';
  }
  return out ? 0 : 2;
}
