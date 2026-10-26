#include "pocket/engine.hpp"
#include "pocket/state.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc, char **argv) {
  const double sr = argc > 1 ? std::atof(argv[1]) : 48000.;
  if (!std::isfinite(sr) || sr < 8000. || sr > 192000.)
    return 1;
  pocket::Engine e;
  e.prepare(sr);
  pocket::Loop loop;
  for (int i = 0; i < 512; ++i)
    loop.append({i / 64., .5f, pocket::Drum(i % 3)});
  e.restore(loop);
  if (argc > 2) {
    std::ifstream file(argv[2]);
    std::vector<double> values;
    double value;
    while (file >> value)
      values.push_back(value);
    pocket::Profile profile;
    if (!pocket::decodeProfile(values.data(), values.size(), profile))
      return 2;
    e.setProfile(profile);
    e.arm();
  }
  std::vector<double> blocks, cpuBlocks;
  struct Slow {
    double cpu, wall, at;
    uint64_t live;
    size_t captured;
  };
  std::vector<Slow> slow;
  const auto cpuTime = []() {
    timespec t{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return double(t.tv_sec) + t.tv_nsec * 1e-9;
  };
  std::array<float, 64> input{};
  uint64_t samples = 0;
  while (true) {
    size_t count = input.size();
    if (argc > 1) {
      std::cin.read(reinterpret_cast<char *>(input.data()), sizeof(input));
      count = size_t(std::cin.gcount()) / sizeof(float);
      if (!count)
        break;
    } else {
      if (samples >= uint64_t(sr * 10.))
        break;
      for (size_t i = 0; i < count; ++i) {
        const double within = std::fmod((samples + i) / sr, .06);
        input[i] =
            float(within < .025
                      ? .7 * std::sin(within * 2. * 3.141592653589793 * 95.) *
                            std::exp(-within * 60.)
                      : 0.);
      }
    }
    const auto hitsBefore = e.hitSerial();
    const auto capturedBefore = e.capture().count;
    const auto start = std::chrono::steady_clock::now();
    const double cpuStart = cpuTime();
    for (size_t i = 0; i < count; ++i) {
      double l, r;
      e.process(input[i], {(samples + i) * 2. / sr, true, true}, l, r);
    }
    const double cpuElapsed = cpuTime() - cpuStart;
    const double elapsed =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start)
            .count();
    blocks.push_back(elapsed); // Research tool only, outside the audio timing.
    cpuBlocks.push_back(cpuElapsed);
    if (cpuElapsed > 64. / sr * .5)
      slow.push_back({cpuElapsed, elapsed, samples / sr,
                      e.hitSerial() - hitsBefore,
                      e.capture().count - capturedBefore});
    samples += count;
  }
  if (blocks.empty())
    return 1;
  double total = 0.;
  for (double t : blocks)
    total += t;
  std::sort(blocks.begin(), blocks.end());
  std::sort(cpuBlocks.begin(), cpuBlocks.end());
  std::sort(slow.begin(), slow.end(),
            [](const Slow &a, const Slow &b) { return a.cpu > b.cpu; });
  std::cout << "{\"audio_seconds\":" << samples / sr
            << ",\"processing_seconds\":" << total << ",\"p99_block_ms\":"
            << blocks[size_t((blocks.size() - 1) * .99)] * 1000.
            << ",\"max_block_ms\":" << blocks.back() * 1000.
            << ",\"p99_cpu_block_ms\":"
            << cpuBlocks[size_t((cpuBlocks.size() - 1) * .99)] * 1000.
            << ",\"max_cpu_block_ms\":" << cpuBlocks.back() * 1000.
            << ",\"block_budget_ms\":" << 64. / sr * 1000.
            << ",\"accepted_live_hits\":" << e.hitSerial()
            << ",\"slow_blocks\":[";
  for (size_t i = 0; i < std::min(size_t(10), slow.size()); ++i) {
    if (i)
      std::cout << ",";
    const auto &b = slow[i];
    std::cout << "{\"at\":" << b.at << ",\"cpu_ms\":" << b.cpu * 1000
              << ",\"wall_ms\":" << b.wall * 1000 << ",\"live\":" << b.live
              << ",\"capture\":" << b.captured << "}";
  }
  std::cout << "]}\n";
}
