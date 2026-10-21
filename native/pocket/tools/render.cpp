#include "pocket/engine.hpp"
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
using namespace pocket;
void word(std::ofstream &f, uint32_t v, int n) {
  for (int i = 0; i < n; ++i)
    f.put(char((v >> (8 * i)) & 255));
}
int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "usage: pocket_render output.wav\n";
    return 1;
  }
  constexpr int sr = 48000, samples = sr * 12;
  std::ofstream f(argv[1], std::ios::binary);
  if (!f)
    return 1;
  f.write("RIFF", 4);
  word(f, 36 + samples * 4, 4);
  f.write("WAVEfmt ", 8);
  word(f, 16, 4);
  word(f, 1, 2);
  word(f, 2, 2);
  word(f, sr, 4);
  word(f, sr * 4, 4);
  word(f, 4, 2);
  word(f, 16, 2);
  f.write("data", 4);
  word(f, samples * 4, 4);
  Engine e;
  e.prepare(sr);
  Loop loop;
  for (int b = 0; b < 8; ++b) {
    loop.append({b + .012f, .58f, Drum::hat});
    loop.append({b + .53f, .32f, Drum::hat});
    if (b % 2 == 0)
      loop.append({double(b), .86f, Drum::kick});
    else
      loop.append({b + .067, .82f, Drum::snare});
  }
  loop.append({3.76, .54f, Drum::kick});
  loop.append({6.73, .3f, Drum::snare});
  loop.append({7.79, .45f, Drum::kick});
  e.restore(loop);
  for (int i = 0; i < samples; ++i) {
    double l, r;
    e.process(0., {double(i) / sr * 2., true, true}, l, r);
    word(f, uint16_t(int16_t(l * 32767.)), 2);
    word(f, uint16_t(int16_t(r * 32767.)), 2);
  }
  std::cout << "Rendered native kit: 120 BPM, late snares, loose hats, three "
               "two-bar cycles.\n";
}
