// Audition the live Auto path on an untrimmed take, with no loop playback.
// stdin: mono float32; argv: sample rate, output stereo float32 path.
#include "pocket/engine.hpp"
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
int main(int argc, char **argv) {
  if (argc != 3)
    return 1;
  const double sr = std::atof(argv[1]);
  if (!std::isfinite(sr) || sr < 8000. || sr > 192000.)
    return 1;
  std::ofstream out(argv[2], std::ios::binary);
  if (!out)
    return 1;
  pocket::Engine engine;
  engine.prepare(sr);
  float input;
  while (std::cin.read(reinterpret_cast<char *>(&input), sizeof(input))) {
    double l, r;
    engine.process(input, {0., false, true}, l, r);
    const float frame[] = {float(l), float(r)};
    out.write(reinterpret_cast<const char *>(frame), sizeof(frame));
  }
  return out ? 0 : 2;
}
