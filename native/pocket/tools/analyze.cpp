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
  const std::string mode = argc > 3 ? argv[3] : "";
  const bool slices = mode == "slices" || mode == "enrollment";
  pocket::SliceExtractor extractor;
  extractor.prepare(sr);
  pocket::Detection pending{};
  std::int64_t start = 0, end = 0;
  double decision = 0.;
  bool active = false;
  const auto emit = [&]() {
    const auto features = extractor.extract(start, end);
    std::cout << pending.beat << "," << int(pending.drum) << "," << pending.velocity << "," << decision;
    for (auto f : pending.features) std::cout << "," << f;
    for (auto f : features.values) std::cout << "," << f;
    for (auto n : features.samples) std::cout << "," << n;
    std::cout << "," << double(end) / sr << "\n";
    active = false;
  };
  pocket::Detector d;
  const auto policy = mode == "enrollment" ? pocket::Detector::Policy::enrolled : argc > 3 && std::string(argv[3]) == "transient"
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
    extractor.push(x);
    const bool complete = d.process(x, double(i) / sr, gate, hit, policy);

    if (complete && slices) {
      if (mode == "enrollment" || hit.drum != pocket::Drum::noise) {
        if (active) { end = std::min(end, std::int64_t(i) + 1 - int(std::round(.010 * sr))); emit(); }
        pending = hit; decision = double(i) / sr;
        start = std::int64_t(i) + 1 - int(std::round(.010 * sr));
        end = start + int(std::round(.080 * sr)); active = true;
      }
    } else if (complete) {
      std::cout << hit.beat << "," << int(hit.drum) << "," << hit.velocity
                << "," << double(i) / sr;
      for (double f : hit.features)
        std::cout << "," << f;
      if (policy == pocket::Detector::Policy::transientCandidates)
        for (double f : hit.bandContrast)
          std::cout << "," << f;
      std::cout << "\n";
    }
    if (active && extractor.position() >= end) {
      const int age = d.pendingAge();
      const auto boundary = extractor.position() - age;
      if (age && boundary > start) end = std::min(end, boundary);
      emit();
    }
    ++i;
  }
  if (active) { end = std::min(end, extractor.position()); emit(); }
}
