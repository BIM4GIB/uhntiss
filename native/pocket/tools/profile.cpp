// Profile parity probe. Profile file is whitespace-separated v4/v5 doubles;
// stdin rows are early17 + late120 + observed lengths(3). No fitting here.
#include "pocket/state.hpp"
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc, char **argv) {
  if (argc != 2)
    return 1;
  std::ifstream file(argv[1]);
  std::vector<double> values;
  double value;
  while (file >> value)
    values.push_back(value);
  pocket::Profile profile;
  if (!file.eof() ||
      !pocket::decodeProfile(values.data(), values.size(), profile))
    return 2;
  pocket::Detection hit{pocket::Drum::noise, .5f, 0.};
  pocket::Slices slices;
  while (std::cin >> hit.features[0]) {
    for (size_t i = 1; i < hit.features.size(); ++i)
      if (!(std::cin >> hit.features[i]))
        return 3;
    for (auto &f : slices.values)
      if (!(std::cin >> f))
        return 3;
    for (auto &n : slices.samples) {
      double count;
      if (!(std::cin >> count) || !pocket::stateInteger(count, 0, 15360))
        return 3;
      n = int(count);
    }
    const auto early = profile.classifyVoice(hit, &slices);
    std::cout << early << ','
              << (early < 0 ? -1 : profile.classifyLate(slices, early)) << '\n';
  }
  return std::cin.eof() ? 0 : 3;
}
