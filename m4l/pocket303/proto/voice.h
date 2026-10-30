// Pocket 303 core — C mirror of the gen~ codebox, written in gen~'s "History" discipline:
// every state variable is READ ONCE at the top (previous sample), computed on locals, and
// WRITTEN ONCE at the end. Keep names and math identical to pocket303.genexpr.
#ifndef VOICE_H
#define VOICE_H
#include <math.h>

typedef struct {
    double cutoff, resonance, envmod, decay, accent, tuning, slide, volume, wave;
} Params;

typedef struct {   // History
    double ph, pitch_s, trig_s, acc_note, meg, acc_raw, acc_env, vca_lvl, vca;
    double s1, s2, s3, s4, fb, hp_x, hp_y;
} Voice;

static inline void voice_init(Voice *v) { *v = (Voice){0}; v->pitch_s = 60.0; }

static inline double polyblep(double t, double dt) {
    double r = 0.0;
    if (t < dt) { double tt = t / dt; r = tt + tt - tt * tt - 1.0; }
    else if (t > 1.0 - dt) { double tt = (t - 1.0) / dt; r = tt * tt + tt + tt + 1.0; }
    return r;
}

// in1 note (MIDI), in2 held (0/1), in3 accent (0/1), in4 slide (0/1), in5 trig counter
static inline double voice_tick(Voice *v, double in1, double in2, double in3, double in4, double in5,
                                const Params *p, double samplerate, double *out3)
{
    double T = 1.0 / samplerate;

    // ---- note events (host does mono note logic; trig counts note-ons) ----
    double newtrig = (in5 != v->trig_s) ? 1.0 : 0.0;
    double legato = (in4 > 0.5) ? 1.0 : 0.0;
    double retrig = (newtrig > 0.5 && legato < 0.5) ? 1.0 : 0.0;
    double gate = (in2 > 0.5) ? 1.0 : 0.0;
    double acc_note = (newtrig > 0.5) ? ((in3 > 0.5) ? 1.0 : 0.0) : v->acc_note;

    // ---- slide: RC glide on the note number (12 ms .. 300 ms) ----
    double slide_tau = 0.012 * pow(25.0, p->slide);
    double pitch_s = (retrig > 0.5) ? in1 : v->pitch_s + (in1 - v->pitch_s) * (1.0 - exp(-T / slide_tau));
    double semis = pitch_s + (p->tuning - 0.5) * 24.0;
    double freq = 440.0 * pow(2.0, (semis - 69.0) / 12.0);

    // ---- oscillator: polyBLEP saw / square ----
    double inc = freq * T;
    double ph = v->ph + inc; ph = ph - floor(ph);
    double saw = 2.0 * ph - 1.0 - polyblep(ph, inc);
    double ph2 = ph + 0.5; ph2 = ph2 - floor(ph2);
    double sq = ((ph < 0.5) ? 1.0 : -1.0) + polyblep(ph, inc) - polyblep(ph2, inc);
    double osc = (p->wave > 0.5) ? sq : saw;

    // ---- MEG: decay-only filter envelope (30 ms .. 2.4 s); accent forces the fast decay ----
    double meg_tau = 0.03 * pow(80.0, p->decay);
    if (acc_note > 0.5 && meg_tau > 0.2) meg_tau = 0.2;
    double meg = ((retrig > 0.5) ? 1.0 : v->meg) * exp(-T / meg_tau);

    // ---- accent sweep: fast decay through a 12 ms RC (the "wow"; stacks on repeats) ----
    double acc_raw = ((retrig > 0.5 && acc_note > 0.5) ? 1.0 : v->acc_raw) * exp(-T / 0.2);
    double acc_env = v->acc_env + (acc_raw * p->accent - v->acc_env) * (1.0 - exp(-T / 0.012));

    // ---- VCA: snap attack (3 ms), slow decay while held (3 s), fast release (10 ms) ----
    double vca_lvl = (retrig > 0.5) ? 1.0 : v->vca_lvl;
    vca_lvl = (gate > 0.5) ? vca_lvl * exp(-T / 3.0) : 0.0;
    double vca_coef = (vca_lvl > v->vca) ? (1.0 - exp(-T / 0.003)) : (1.0 - exp(-T / 0.010));
    double vca = v->vca + (vca_lvl - v->vca) * vca_coef;
    double amp = vca * (1.0 + 0.7 * acc_env);

    // ---- cutoff: knob 150 Hz .. 4.8 kHz, envelope up to 2.4 octaves, accent up to +0.5 ----
    double cut_hz = 150.0 * pow(2.0, p->cutoff * 5.0);
    double env_oct = meg * p->envmod * 2.4 + acc_env * 0.5;
    double fc = cut_hz * pow(2.0, env_oct);
    fc = (fc > 8000.0) ? 8000.0 : ((fc < 20.0) ? 20.0 : fc);
    double k = p->resonance * 4.0;

    // ---- 4-pole ladder, 2x oversampled, soft-saturated input, half-sample feedback ----
    double g = 1.0 - exp(-6.283185307179586 * fc / (samplerate * 2.0));
    double s1 = v->s1, s2 = v->s2, s3 = v->s3, s4 = v->s4, fb = v->fb, y = 0.0;
    for (int i = 0; i < 2; i++) {
        double u = osc * 0.5 - k * fb;
        double x = 2.0 * tanh(0.5 * u);
        s1 = s1 + g * (x - s1);
        s2 = s2 + g * (s1 - s2);
        s3 = s3 + g * (s2 - s3);
        double s4n = s4 + g * (s3 - s4);
        fb = 0.5 * (s4n + s4);
        s4 = s4n;
        y = s4;
    }

    // ---- output: resonance make-up, soft clip, DC block, VCA, volume ----
    y = tanh(y * (1.0 + 0.25 * k));
    double hp = y - v->hp_x + 0.997 * v->hp_y;
    double out = hp * amp * p->volume * p->volume;

    // ---- write History ----
    v->trig_s = in5; v->acc_note = acc_note; v->pitch_s = pitch_s; v->ph = ph;
    v->meg = meg; v->acc_raw = acc_raw; v->acc_env = acc_env; v->vca_lvl = vca_lvl; v->vca = vca;
    v->s1 = s1; v->s2 = s2; v->s3 = s3; v->s4 = s4; v->fb = fb; v->hp_x = y; v->hp_y = hp;
    *out3 = gate;
    return out;
}
#endif
