// Research bridge: stdin mono float32 PCM, argv[1] sample rate. No audio SDK.
#include "pocket/engine.hpp"
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>
int main(int argc, char **argv) {
  if (argc < 2)
    return 1;
  double sr = std::atof(argv[1]), gate = argc > 2 ? std::atof(argv[2]) : .010;
  if (sr < 8000. || sr > 192000.)
    return 1;
  pocket::Detector d;
  const auto policy = argc > 3 && std::string(argv[3]) == "transient"
                          ? pocket::Detector::Policy::transientCandidates
                      : argc > 3 && std::string(argv[3]) == "candidates"
                          ? pocket::Detector::Policy::candidates
                          : pocket::Detector::Policy::automatic;
  d.prepare(sr);
  pocket::Detection hit;
  float x;
  uint64_t i = 0;
  std::cout << std::setprecision(10);
  while (std::cin.read(reinterpret_cast<char *>(&x), sizeof(x))) {
    if (d.process(x, double(i) / sr, gate, hit, policy)) {
      std::cout << hit.beat << "," << int(hit.drum) << "," << hit.velocity
                << "," << double(i) / sr;
      for (double f : hit.features)
        std::cout << "," << f;
      if (policy == pocket::Detector::Policy::transientCandidates)
        for (double f : hit.bandContrast)
          std::cout << "," << f;
      std::cout << "\n";
    }
    ++i;
  }
}
