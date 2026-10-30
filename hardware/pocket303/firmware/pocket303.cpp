// Pocket 303 — placeholder voice for the Daisy Seed.
//
// Proves the MIDI path (USB MIDI from the Mac, or DIN/TRS MIDI on USART1 RX = D14)
// and the audio path (Audio Out L/R) before the real gen~ core arrives from m4l/.
//
//   saw | square  ->  4-pole ladder (DaisySP LadderFilter)  ->  VCA  ->  soft clip
//
// 303-isms kept on purpose: decay-only filter envelope, accent on velocity >= 100
// (shorter decay, more envelope, and a slow "wow" envelope that builds on repeated
// accents), slide when notes overlap (legato: pitch glides, envelopes do not retrigger).
// It is a bench tool, not the final sound.
//
// Controls (MIDI CC, any channel; pots override when built with POTS=1):
//   CC 74 cutoff   CC 71 resonance   CC 12 env mod   CC 75 decay
//   CC 76 accent   CC 77 tuning      CC 5  slide     CC 7  volume
//   CC 70 wave (>= 64 = square)      CC 120/123 all notes off
// MIDI System Reset (0xFF) reboots into the DFU bootloader — same convention as Oopsy.

#include "daisy_seed.h"
#include "daisysp.h"
#include <cmath>

using namespace daisy;
using namespace daisysp;

#ifndef POCKET303_POTS
#define POCKET303_POTS 0
#endif

namespace
{
constexpr size_t kBlockSize = 16; // 48 kHz / 16 = 3 kHz control rate, 0.33 ms latency

enum Param : uint8_t
{
    CUTOFF,
    RESO,
    ENVMOD,
    DECAY,
    ACCENT,
    TUNE,
    SLIDE,
    VOLUME,
    NUM_PARAMS
};

// Pin plan — must match ../oopsy/pocket303.json and ../wiring.md
constexpr Pin kPotPin[NUM_PARAMS]
    = {seed::A0, seed::A1, seed::A2, seed::A3, seed::A4, seed::A5, seed::A6, seed::A7};
constexpr Pin kWavePin    = seed::D0; // toggle to GND, internal pull-up: closed = square
constexpr Pin kGateLedPin = seed::D1; // LED + 1k to GND

constexpr uint8_t kCc[NUM_PARAMS] = {74, 71, 12, 75, 76, 77, 5, 7};
constexpr uint8_t kCcWave         = 70;
constexpr uint8_t kAccentVelocity = 100;

DaisySeed       hw;
MidiUartHandler midi_uart;
MidiUsbHandler  midi_usb;
Oscillator      osc;
LadderFilter    flt;
Switch          wave_sw;
GPIO            gate_led;
#if POCKET303_POTS
AnalogControl pots[NUM_PARAMS];
#endif

// ---- shared state: main loop writes, audio interrupt reads (single core) ----
volatile float params[NUM_PARAMS] = {0.45f, 0.55f, 0.5f, 0.4f, 0.6f, 0.5f, 0.3f, 0.8f};
volatile bool  cc_square          = false;

struct NoteCmd
{
    volatile uint32_t serial = 0; // bumped last, after the fields below
    volatile uint8_t  note   = 48;
    volatile bool     gate   = false;
    volatile bool     accent = false;
    volatile bool     slide  = false; // legato: glide, do not retrigger
} cmd;

// ---- note stack (last-note priority, legato back to the held note) ----
uint8_t held[16];
int     n_held = 0;

void Publish(uint8_t note, bool gate, bool accent, bool slide)
{
    cmd.note   = note;
    cmd.gate   = gate;
    cmd.accent = accent;
    cmd.slide  = slide;
    cmd.serial = cmd.serial + 1;
}

void RemoveHeld(uint8_t note)
{
    for(int i = 0; i < n_held; i++)
    {
        if(held[i] == note)
        {
            for(int j = i; j < n_held - 1; j++)
                held[j] = held[j + 1];
            n_held--;
            return;
        }
    }
}

void OnNoteOn(uint8_t note, uint8_t vel)
{
    const bool legato = n_held > 0;
    RemoveHeld(note);
    if(n_held < (int)sizeof(held))
        held[n_held++] = note;
    Publish(note, true, vel >= kAccentVelocity, legato);
}

void OnNoteOff(uint8_t note)
{
    RemoveHeld(note);
    if(n_held > 0)
        Publish(held[n_held - 1], true, cmd.accent, true);
    else
        Publish(note, false, false, false);
}

void AllNotesOff()
{
    n_held = 0;
    Publish(cmd.note, false, false, false);
}

void HandleMidi(MidiEvent m)
{
    switch(m.type)
    {
        case MidiMessageType::NoteOn:
        {
            NoteOnEvent e = m.AsNoteOn();
            if(e.velocity == 0)
                OnNoteOff(e.note);
            else
                OnNoteOn(e.note, e.velocity);
        }
        break;
        case MidiMessageType::NoteOff: OnNoteOff(m.AsNoteOff().note); break;
        case MidiMessageType::ControlChange:
        {
            ControlChangeEvent c = m.AsControlChange();
            for(int i = 0; i < NUM_PARAMS; i++)
                if(c.control_number == kCc[i])
                    params[i] = c.value * (1.f / 127.f);
            if(c.control_number == kCcWave)
                cc_square = c.value >= 64;
            if(c.control_number == 120 || c.control_number == 123)
                AllNotesOff();
        }
        break;
        case MidiMessageType::SystemRealTime:
            if(m.srt_type == SystemRealTimeType::Reset)
                System::ResetToBootloader(); // 0xFF -> DFU, no buttons needed
            break;
        default: break;
    }
}

// ---- the voice ----
struct Voice
{
    uint32_t seen = 0;
    float    note_cur = 48.f, note_tgt = 48.f;
    bool     gate = false, accent = false;
    bool     f_attack = false, a_attack = false;
    float    env_f = 0.f; // filter envelope (decay only)
    float    env_a = 0.f; // VCA envelope
    float    acc   = 0.f; // accent "wow" envelope, charges on repeated accents

    void Take(const NoteCmd& c)
    {
        note_tgt = c.note;
        accent   = c.accent;
        if(!c.gate)
        {
            gate = false;
            return;
        }
        if(!c.slide || !gate)
        {
            note_cur = note_tgt; // fresh note: no glide, retrigger
            f_attack = a_attack = true;
        }
        gate = true;
    }
} voice;

inline float OnePoleCoef(float seconds, float sr)
{
    return 1.f - expf(-1.f / (seconds * sr));
}

void AudioCallback(AudioHandle::InputBuffer  in,
                   AudioHandle::OutputBuffer out,
                   size_t                    size)
{
    const float sr = hw.AudioSampleRate();

    // --- controls, once per block ---
    wave_sw.Debounce();
    float p[NUM_PARAMS];
    for(int i = 0; i < NUM_PARAMS; i++)
        p[i] = params[i];
#if POCKET303_POTS
    for(int i = 0; i < NUM_PARAMS; i++)
        p[i] = pots[i].Process();
#endif
    const bool square = wave_sw.Pressed() || cc_square;
    osc.SetWaveform(square ? Oscillator::WAVE_POLYBLEP_SQUARE
                           : Oscillator::WAVE_POLYBLEP_SAW);

    if(cmd.serial != voice.seen)
    {
        voice.seen = cmd.serial;
        voice.Take(cmd);
    }

    const float base_oct = log2f(80.f) + p[CUTOFF] * 6.64f;  // 80 Hz .. ~8 kHz
    const float env_oct  = p[ENVMOD] * 4.5f;                 // up to 4.5 octaves
    const float acc_oct  = p[ACCENT] * 2.0f;                 // accent "wow" reach
    const float acc_gain = p[ACCENT] * 0.8f;                 // accent VCA boost
    const float tune     = (p[TUNE] - 0.5f) * 2.f;           // +/- 1 semitone
    const float volume   = p[VOLUME] * p[VOLUME];
    const float decay_s  = voice.accent ? 0.2f : 0.2f * powf(10.f, p[DECAY]); // 0.2..2 s
    const float slide_s  = 0.02f + 0.4f * p[SLIDE] * p[SLIDE];                // 20..420 ms

    const float f_dec   = expf(-1.f / (decay_s * sr));
    const float att     = OnePoleCoef(0.002f, sr);
    const float a_hold  = expf(-1.f / (2.5f * sr));  // 303 VCA sags slowly while held
    const float a_rel   = expf(-1.f / (0.006f * sr));
    const float acc_up  = OnePoleCoef(0.010f, sr);
    const float acc_dn  = OnePoleCoef(0.250f, sr);
    const float glide   = OnePoleCoef(slide_s, sr);

    flt.SetRes(p[RESO] * 1.35f);

    for(size_t i = 0; i < size; i++)
    {
        Voice& v = voice;

        v.note_cur += (v.note_tgt - v.note_cur) * glide;
        osc.SetFreq(mtof(v.note_cur + tune));

        if(v.f_attack)
        {
            v.env_f += (1.05f - v.env_f) * att;
            if(v.env_f >= 1.f)
            {
                v.env_f    = 1.f;
                v.f_attack = false;
            }
        }
        else
            v.env_f *= f_dec;

        const float acc_target = v.accent && v.gate ? v.env_f : 0.f;
        v.acc += (acc_target - v.acc) * (acc_target > v.acc ? acc_up : acc_dn);

        if(v.a_attack)
        {
            v.env_a += (1.05f - v.env_a) * att;
            if(v.env_a >= 1.f)
            {
                v.env_a    = 1.f;
                v.a_attack = false;
            }
        }
        else
            v.env_a *= v.gate ? a_hold : a_rel;

        float cut = exp2f(base_oct + env_oct * v.env_f + acc_oct * v.acc);
        flt.SetFreq(fclamp(cut, 20.f, 18000.f));

        float y = flt.Process(osc.Process());
        y       = tanhf(y * (1.4f + acc_gain * v.acc)) * v.env_a * volume;

        out[0][i] = y;
        out[1][i] = y;
    }
}
} // namespace

int main(void)
{
    hw.Init(true); // boost: 480 MHz
    hw.SetAudioBlockSize(kBlockSize);
    hw.SetAudioSampleRate(SaiHandle::Config::SampleRate::SAI_48KHZ);
    const float sr = hw.AudioSampleRate();

    osc.Init(sr);
    osc.SetWaveform(Oscillator::WAVE_POLYBLEP_SAW);
    osc.SetAmp(1.f);
    osc.SetPw(0.5f);

    flt.Init(sr);
    flt.SetFilterMode(LadderFilter::FilterMode::LP24);
    flt.SetInputDrive(1.0f);
    flt.SetPassbandGain(0.3f);

    wave_sw.Init(kWavePin,
                 hw.AudioCallbackRate(),
                 Switch::TYPE_TOGGLE,
                 Switch::POLARITY_INVERTED,
                 GPIO::Pull::PULLUP);
    gate_led.Init(kGateLedPin, GPIO::Mode::OUTPUT);

#if POCKET303_POTS
    AdcChannelConfig cfg[NUM_PARAMS];
    for(int i = 0; i < NUM_PARAMS; i++)
        cfg[i].InitSingle(kPotPin[i]);
    hw.adc.Init(cfg, NUM_PARAMS);
    hw.adc.Start();
    for(int i = 0; i < NUM_PARAMS; i++)
        pots[i].Init(hw.adc.GetPtr(i), hw.AudioCallbackRate());
#endif

    MidiUartHandler::Config uart_cfg; // defaults: USART1, RX = D14 (PB7), TX = D13 (PB6)
    midi_uart.Init(uart_cfg);
    midi_uart.StartReceive();

    MidiUsbHandler::Config usb_cfg; // shows up as "Daisy Seed Built In" on the Mac
    usb_cfg.transport_config.periph = MidiUsbTransport::Config::INTERNAL;
    midi_usb.Init(usb_cfg);
    midi_usb.StartReceive();

    hw.StartAudio(AudioCallback);

    for(;;)
    {
        midi_uart.Listen();
        while(midi_uart.HasEvents())
            HandleMidi(midi_uart.PopEvent());
        midi_usb.Listen();
        while(midi_usb.HasEvents())
            HandleMidi(midi_usb.PopEvent());

        hw.SetLed(cmd.gate);
        gate_led.Write(cmd.gate);
    }
}
