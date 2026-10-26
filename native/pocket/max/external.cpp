#include "ext.h"
#include "ext_obex.h"
#include "pocket/engine.hpp"
#include "pocket/state.hpp"
#include "z_dsp.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <memory>
#include <mutex>
#include <new>
#include <vector>
using namespace pocket;
namespace {
static_assert(std::atomic<double>::is_always_lock_free,
              "Audio controls require lock-free double atomics");
enum class Action { record, undo, clear, tap, restore, learn, profile };
struct Command {
  Action action{};
  int drum = 0;
  float velocity = .85f;
  Loop loop{};
  Profile profile{};
  uint64_t receipt = 0;
};
struct Snapshot {
  Loop active{}, take{};
  Profile profile{};
  int learning = -1, remaining = 0;
  int state = 0, undo = 0, drum = -1;
  bool running = false;
  double position = 0., level = 0.;
  uint64_t revision = 0, serial = 0, receipt = 0;
  double sampleRate = 48000.;
};
struct Data {
  Engine engine;
  HostClock hostClock;
  Queue<Command, 8> commands;
  Queue<Engine::MidiHit, 2048> midi;
  Queue<Snapshot, 4> snapshots;
  std::mutex producers; // Only message threads enter this mutex.
  std::atomic<double> ppq{0.}, input{1.}, output{.75}, threshold{.010},
      tone{.5}, decay{.5}, drive{.25};
  std::atomic<int> voice{-1};
  std::atomic<bool> running{false}, fourFour{true}, monitor{true},
      playback{true}, overdub{false}, request{true};
  Command audioCommand{};
  Snapshot audioSnapshot{};
  Snapshot shown{};
  Loop saved{};
  Profile savedProfile{};
  bool savedValid = false;
  uint64_t savedRevision = 0, appliedReceipt = 0, shownReceipt = 0;
  std::atomic<uint64_t> nextReceipt{1};
  double sampleRate = 48000., notifiedRate = 0.;
};
struct Object {
  t_pxobject object;
  Data *data;
  void *messages;
  void *clock;
  void *qelem;
  void *midiOut;
  void *midiClock;
};
t_class *pocketClass = nullptr;
bool enqueue(Object *x, const Command &command) {
  std::lock_guard<std::mutex> lock(x->data->producers);
  if (!x->data->commands.push(command)) {
    object_error((t_object *)x, "control queue full; command rejected");
    return false;
  }
  return true;
}
void record(Object *x) {
  Command c;
  c.action = Action::record;
  enqueue(x, c);
}
void undo(Object *x) {
  Command c;
  c.action = Action::undo;
  enqueue(x, c);
}
void clear(Object *x) {
  Command c;
  c.action = Action::clear;
  enqueue(x, c);
}
void tap(Object *x, long drum, double velocity) {
  Command c;
  c.action = Action::tap;
  c.drum = int(drum);
  c.velocity = float(velocity);
  enqueue(x, c);
}
void learn(Object *x, long drum) {
  Command c;
  c.action = Action::learn;
  c.drum = int(drum);
  enqueue(x, c);
}
void hostbeat(Object *x, double value) {
  if (std::isfinite(value))
    x->data->ppq.store(value, std::memory_order_relaxed);
}
void running(Object *x, long value) {
  x->data->running.store(value != 0, std::memory_order_relaxed);
}
void meter(Object *x, long numerator, long denominator) {
  x->data->fourFour.store(numerator == 4 && denominator == 4,
                          std::memory_order_relaxed);
}
void setting(Object *x, t_symbol *, long argc, t_atom *argv) {
  if (argc < 2 || atom_gettype(argv) != A_SYM)
    return;
  const auto name = atom_getsym(argv);
  double value = atom_getfloat(argv + 1);
  if (!std::isfinite(value))
    return;
  auto &d = *x->data;
  if (name == gensym("voice"))
    d.voice.store(int(std::clamp(value, 0., 3.)) - 1);
  else if (name == gensym("input"))
    d.input.store(std::clamp(value, .1, 8.));
  else if (name == gensym("output"))
    d.output.store(std::clamp(value, 0., 1.));
  else if (name == gensym("threshold"))
    d.threshold.store(std::clamp(value, .002, .2));
  else if (name == gensym("tone"))
    d.tone.store(std::clamp(value, 0., 1.));
  else if (name == gensym("decay"))
    d.decay.store(std::clamp(value, 0., 1.));
  else if (name == gensym("drive"))
    d.drive.store(std::clamp(value, 0., 1.));
  else if (name == gensym("monitor"))
    d.monitor.store(value != 0.);
  else if (name == gensym("playback"))
    d.playback.store(value != 0.);
  else if (name == gensym("overdub"))
    d.overdub.store(value != 0.);
}
void perform(Object *x, t_object *, double **ins, long, double **outs, long,
             long frames, long, void *) {
  auto &d = *x->data;
  Settings s;
  s.inputGain = d.input.load(std::memory_order_relaxed);
  s.outputGain = d.output.load(std::memory_order_relaxed);
  s.threshold = d.threshold.load(std::memory_order_relaxed);
  s.tone = d.tone.load(std::memory_order_relaxed);
  s.decay = d.decay.load(std::memory_order_relaxed);
  s.drive = d.drive.load(std::memory_order_relaxed);
  s.monitor = d.monitor.load(std::memory_order_relaxed);
  s.playback = d.playback.load(std::memory_order_relaxed);
  s.voice = d.voice.load(std::memory_order_relaxed);
  s.mode =
      d.overdub.load(std::memory_order_relaxed) ? Mode::overdub : Mode::replace;
  d.engine.setSettings(s);
  auto &c = d.audioCommand;
  // Bounded work even if MIDI floods the control queue.
  for (int i = 0; i < 7 && d.commands.pop(c); ++i) {
    switch (c.action) {
    case Action::record:
      d.engine.arm();
      break;
    case Action::undo:
      d.engine.undo();
      break;
    case Action::clear:
      d.engine.clear();
      break;
    case Action::tap:
      d.engine.tap(Drum(c.drum), c.velocity);
      break;
    case Action::restore:
      d.engine.restore(c.loop);
      d.engine.setProfile(c.profile);
      break;
    case Action::learn:
      d.engine.learn(c.drum);
      break;
    case Action::profile:
      d.engine.setProfile(c.profile);
      d.appliedReceipt = c.receipt;
      break;
    }
  }
  const bool run = d.running.load(std::memory_order_relaxed),
             four = d.fourFour.load(std::memory_order_relaxed);
  if (frames)
    d.hostClock.begin(d.ppq.load(std::memory_order_relaxed), ins[1][0], run,
                      four);
  for (long i = 0; i < frames; ++i)
    d.engine.process(ins[0][i], d.hostClock.next(ins[1][i]), outs[0][i],
                     outs[1][i]);
  Engine::MidiHit midi;
  bool emitted = false;
  while (d.engine.popMidi(midi))
    emitted = d.midi.push(midi) || emitted;
  if (emitted)
    clock_delay(x->midiClock, 0.);
  if (d.request.exchange(false, std::memory_order_relaxed)) {
    auto &snap = d.audioSnapshot;
    snap.profile = d.engine.profile();
    snap.receipt = d.appliedReceipt;
    snap.sampleRate = d.sampleRate;
    snap.learning = d.engine.learning();
    snap.remaining = d.engine.learnRemaining();
    snap.active = d.engine.loop();
    snap.take = d.engine.capture();
    snap.state = int(d.engine.state());
    snap.undo = int(d.engine.undoCount());
    snap.drum = d.engine.lastDrum();
    snap.running = d.engine.running();
    snap.position = d.engine.position();
    snap.level = d.engine.level();
    snap.revision = d.engine.revision();
    snap.serial = d.engine.hitSerial();
    if (!d.snapshots.push(snap))
      d.request.store(true, std::memory_order_relaxed);
  }
}
void dsp(Object *x, t_object *dsp64, short *, double sr, long, long) {
  x->data->engine.prepare(sr);
  x->data->sampleRate = sr;
  x->data->hostClock.reset();
  object_method(dsp64, gensym("dsp_add64"), x, perform, 0, nullptr);
}
void emitEvents(Object *x, const Loop &loop) {
  std::vector<t_atom> atoms(loop.count * 3);
  for (size_t i = 0; i < loop.count; ++i) {
    atom_setfloat(&atoms[i * 3], loop.events[i].beat);
    atom_setfloat(&atoms[i * 3 + 1], loop.events[i].velocity);
    atom_setlong(&atoms[i * 3 + 2], loop.events[i].voice >= 0
                                        ? loop.events[i].voice
                                        : int(loop.events[i].drum));
  }
  outlet_anything(x->messages, gensym("events"), short(atoms.size()),
                  atoms.data());
}
void poll(Object *x) {
  auto &d = *x->data;
  Snapshot snap;
  bool changed = false;
  while (d.snapshots.pop(snap)) {
    d.shown = snap;
    changed = true;
  }
  if (changed) {
    auto &v = d.shown;
    t_atom atoms[12];
    atom_setlong(atoms, v.state);
    atom_setlong(atoms + 1, v.running);
    atom_setfloat(atoms + 2, v.position);
    atom_setfloat(atoms + 3, v.level);
    atom_setlong(atoms + 4, v.undo);
    atom_setlong(atoms + 5, v.drum);
    atom_setlong(atoms + 6, t_atom_long(v.serial));
    atom_setlong(atoms + 7, t_atom_long(v.active.count));
    atom_setlong(atoms + 8, v.learning);
    atom_setlong(atoms + 9, v.remaining);
    atom_setlong(atoms + 10, v.profile.ready());
    atom_setlong(atoms + 11,
                 v.profile.delayed() && v.profile.sampleRate == v.sampleRate);
    outlet_anything(x->messages, gensym("status"), 12, atoms);
    if ((v.receipt && v.receipt != d.shownReceipt) ||
        (v.profile.sampleRate > 0. && d.notifiedRate != v.sampleRate)) {
      d.shownReceipt = v.receipt;
      t_atom receipt[3];
      atom_setlong(receipt, v.profile.voices);
      atom_setfloat(receipt + 1, v.profile.sampleRate);
      atom_setlong(receipt + 2, v.profile.rejects);
      outlet_anything(
          x->messages,
          gensym(v.profile.sampleRate && v.profile.sampleRate != v.sampleRate
                     ? "profile_rate_mismatch"
                     : "profile_loaded"),
          3, receipt);
    }
    d.notifiedRate = v.sampleRate;
    emitEvents(x, v.state == int(State::recording) ? v.take : v.active);
    if (!d.savedValid || v.revision != d.savedRevision) {
      d.saved = v.active;
      d.savedProfile = v.profile;
      d.savedValid = true;
      d.savedRevision = v.revision;
      std::vector<t_atom> voices(v.profile.voices * 2);
      for (int i = 0; i < v.profile.voices; ++i) {
        atom_setsym(&voices[i * 2], gensym(v.profile.names[i].data()));
        atom_setlong(&voices[i * 2 + 1], v.profile.notes[i]);
      }
      outlet_anything(x->messages, gensym("voices"), short(voices.size()),
                      voices.data());
      object_notify((t_object *)x, gensym("modified"), nullptr);
    }
  }
}
void tick(Object *x) {
  x->data->request.store(true, std::memory_order_relaxed);
  qelem_set(x->qelem);
  clock_delay(x->clock, 33.);
}
// `profile <numbers>`: install a legacy v4 or versioned v5 enrollment. Replaces
// the voice examples only; the loop is untouched. Persists with the Set through
// the pattr state like a taught profile does.
void profile(Object *x, t_symbol *, long argc, t_atom *argv) {
  if (argc <= 0 || argc > long(maxProfileValues)) {
    object_error((t_object *)x, "profile: invalid payload length %ld", argc);
    outlet_anything(x->messages, gensym("profile_error"), 0, nullptr);
    return;
  }
  std::vector<double> values(size_t(argc), 0.);
  for (long i = 0; i < argc; ++i) {
    if (atom_gettype(argv + i) != A_LONG && atom_gettype(argv + i) != A_FLOAT) {
      object_error((t_object *)x, "profile: value %ld is not a number", i);
      outlet_anything(x->messages, gensym("profile_error"), 0, nullptr);
      return;
    }
    values[i] = atom_getfloat(argv + i);
  }
  auto c = std::make_unique<Command>();
  c->action = Action::profile;
  if (!decodeProfile(values.data(), size_t(argc), c->profile)) {
    object_error((t_object *)x, "profile: invalid v4/v5 payload");
    outlet_anything(x->messages, gensym("profile_error"), 0, nullptr);
    return;
  }
  c->receipt = x->data->nextReceipt.fetch_add(1);
  if (!enqueue(x, *c)) {
    outlet_anything(x->messages, gensym("profile_error"), 0, nullptr);
    return;
  }
  outlet_anything(x->messages, gensym("profile_queued"), 0, nullptr);
}
t_max_err getvalue(Object *x, long *argc, t_atom **argv) {
  auto values = std::make_unique<StateValues>();
  const long required =
      long(encodeState(x->data->saved, x->data->savedProfile, *values));
  if (!*argv || *argc < required) {
    if (*argv)
      sysmem_freeptr(*argv);
    *argv = (t_atom *)sysmem_newptr(required * sizeof(t_atom));
  }
  if (!*argv) {
    *argc = 0;
    return MAX_ERR_OUT_OF_MEM;
  }
  *argc = required;
  for (long i = 0; i < required; ++i)
    atom_setfloat(*argv + i, (*values)[i]);
  return MAX_ERR_NONE;
}
t_max_err setvalue(Object *x, long argc, t_atom *argv) {
  if (argc < 0 || argc > long(maxStateValues))
    return MAX_ERR_GENERIC;
  std::vector<double> values(size_t(argc), 0.);
  for (long i = 0; i < argc; ++i) {
    if (atom_gettype(argv + i) != A_LONG && atom_gettype(argv + i) != A_FLOAT)
      return MAX_ERR_GENERIC;
    values[i] = atom_getfloat(argv + i);
  }
  auto c = std::make_unique<Command>();
  c->action = Action::restore;
  if (!decodeState(values.data(), size_t(argc), c->loop, c->profile) ||
      !enqueue(x, *c))
    return MAX_ERR_GENERIC;
  x->data->savedProfile = c->profile;
  x->data->saved = c->loop;
  x->data->savedValid = true;
  return MAX_ERR_NONE;
}
// A high-priority clock drains note events after the DSP vector. This is
// separate from the 33 ms display poll; never invoke message boxes in perform.
void midiTick(Object *x) {
  Engine::MidiHit hit;
  for (int i = 0; i < 2047 && x->data->midi.pop(hit); ++i) {
    t_atom values[2];
    atom_setlong(values, hit.note);
    atom_setlong(values + 1,
                 std::clamp(int(std::round(hit.velocity * 127)), 1, 127));
    outlet_list(x->midiOut, nullptr, 2, values);
  }
}
void assist(Object *, void *, long io, long index, char *text) {
  if (io == ASSIST_INLET)
    snprintf_zero(text, 256,
                  index ? "Host beat phase (plugphasor~)"
                        : "Mic audio / record, undo, clear, tap, setting");
  else
    snprintf_zero(text, 256,
                  index == 3   ? "MIDI note / velocity"
                  : index == 2 ? "Display/status messages"
                               : (index ? "Right output" : "Left output"));
}
void destroy(Object *x) {
  dsp_free(&x->object);
  if (x->clock)
    object_free(x->clock);
  if (x->midiClock)
    object_free(x->midiClock);
  if (x->qelem)
    qelem_free(x->qelem);
  delete x->data;
}
void *create(t_symbol *, long, t_atom *) {
  auto *x = (Object *)object_alloc(pocketClass);
  if (!x)
    return nullptr;
  x->clock = x->qelem = x->midiClock = nullptr;
  dsp_setup(&x->object, 2);
  x->data = new (std::nothrow) Data;
  if (!x->data) {
    object_free(x);
    return nullptr;
  }
  x->object.z_misc |= Z_NO_INPLACE;
  x->midiOut = outlet_new(x, nullptr);
  x->messages = outlet_new(x, nullptr);
  outlet_new(x, "signal");
  outlet_new(x, "signal");
  x->qelem = qelem_new(x, (method)poll);
  x->clock = clock_new(x, (method)tick);
  x->midiClock = clock_new(x, (method)midiTick);
  clock_delay(x->clock, 33.);
  return x;
}
} // namespace
extern "C" C74_EXPORT void ext_main(void *) {
  auto *c = class_new("uhntiss.pocket06~", (method)create, (method)destroy,
                      sizeof(Object), nullptr, A_GIMME, 0);
  class_addmethod(c, (method)dsp, "dsp64", A_CANT, 0);
  class_addmethod(c, (method)assist, "assist", A_CANT, 0);
  class_addmethod(c, (method)record, "record", 0);
  class_addmethod(c, (method)undo, "undo", 0);
  class_addmethod(c, (method)clear, "clear", 0);
  class_addmethod(c, (method)learn, "learn", A_LONG, 0);
  class_addmethod(c, (method)profile, "profile", A_GIMME, 0);
  class_addmethod(c, (method)tap, "tap", A_LONG, A_DEFFLOAT, 0);
  class_addmethod(c, (method)hostbeat, "hostbeat", A_FLOAT, 0);
  class_addmethod(c, (method)running, "running", A_LONG, 0);
  class_addmethod(c, (method)meter, "meter", A_LONG, A_LONG, 0);
  class_addmethod(c, (method)setting, "setting", A_GIMME, 0);
  class_addmethod(c, (method)getvalue, "getvalueof", A_CANT, 0);
  class_addmethod(c, (method)setvalue, "setvalueof", A_CANT, 0);
  class_dspinit(c);
  class_register(CLASS_BOX, c);
  pocketClass = c;
}
