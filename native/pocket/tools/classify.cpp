// Offline export-parity bridge: 17 space-separated features per row on stdin.
#include "pocket/engine.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
int main(int argc, char **argv) {
  const double sr = argc > 1 ? std::atof(argv[1]) : 48000.;
  pocket::Features features{};
  while (std::cin >> features[0]) {
    for (size_t i = 1; i < features.size(); ++i)
      if (!(std::cin >> features[i]))
        return 1;
    for (double value : features)
      if (!std::isfinite(value))
        return 1;
    std::cout << int(pocket::classifyAttack(features, sr)) << '\n';
  }
  return std::cin.eof() ? 0 : 1;
}
