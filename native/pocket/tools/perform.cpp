// Real Engine replay with an imported profile. No labels or fitting in DSP.
// stdin mono f32; argv rate, profile.txt. Capture spans 64 seconds of audio.
#include "pocket/state.hpp"
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <vector>
int main(int argc, char **argv) {
  if (argc != 3)
    return 1;
  const double sr = std::atof(argv[1]);
  if (!std::isfinite(sr) || sr < 8000. || sr > 192000.)
    return 1;
  std::ifstream file(argv[2]);
  std::vector<double> values;
  double value;
  while (file >> value)
    values.push_back(value);
  pocket::Profile p;
  if (!file.eof() || !pocket::decodeProfile(values.data(), values.size(), p))
    return 2;
  auto e = std::make_unique<pocket::Engine>();
  e->prepare(sr);
  e->setProfile(p);
  auto settings = e->settings();
  settings.playback = false;
  e->setSettings(settings);
  e->arm();
  std::cout << std::setprecision(12);
  std::uint64_t i = 0;
  const auto sample = [&](float x) {
    double l, r;
    e->process(x, {double(i) / sr / 8., true, true}, l, r);
    pocket::Engine::MidiHit hit;
    while (e->popMidi(hit))
      std::cout << "live," << e->lastHitBeat() * 8. << ',' << hit.note << ','
                << hit.velocity << ',' << double(i) / sr << '\n';
    ++i;
  };
  float x;
  while (std::cin.read(reinterpret_cast<char *>(&x), sizeof(x))) {
    if (i >= std::uint64_t(63.9 * sr))
      return 3;
    sample(x);
  }
  for (int j = 0; j < int(.081 * sr); ++j)
    sample(0.);
  for (size_t j = 0; j < e->capture().count; ++j) {
    const auto &hit = e->capture().events[j];
    std::cout << "stored," << hit.beat * 8. << ',' << hit.note << ','
              << hit.velocity << ',' << double(i - 1) / sr << '\n';
  }
}
