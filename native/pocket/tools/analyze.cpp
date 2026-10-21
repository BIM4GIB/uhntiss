// Research bridge: stdin mono float32 PCM, argv[1] sample rate. No audio SDK.
#include "pocket/engine.hpp"
#include <cstdlib>
#include <iomanip>
#include <iostream>
int main(int argc, char **argv) {
  if (argc < 2)
    return 1;
  double sr = std::atof(argv[1]), gate = argc > 2 ? std::atof(argv[2]) : .018;
  if (sr < 8000. || sr > 192000.)
    return 1;
  pocket::Detector d;
  d.prepare(sr);
  pocket::Detection hit;
  float x;
  uint64_t i = 0;
  std::cout << std::setprecision(10);
  while (std::cin.read(reinterpret_cast<char *>(&x), sizeof(x))) {
    if (d.process(x, double(i) / sr, gate, hit))
      std::cout << hit.beat << "," << int(hit.drum) << "," << hit.velocity
                << "," << double(i) / sr << "," << hit.features[0] << ","
                << hit.features[1] << "," << hit.features[2] << ","
                << hit.features[3] << "\n";
    ++i;
  }
}
