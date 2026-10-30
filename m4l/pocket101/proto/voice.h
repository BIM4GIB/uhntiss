// Pocket 101 - C mirror of pocket101.genexpr (SH-101 style mono bass synth).
// Written in gen~ "History" discipline (read state once at the top, compute on locals, write once at the
// end) so the GenExpr port is line for line. Inputs are the Pocket 303's (same js): in1 note, in2 held,
// in3 unused (velocity accent - the 101 has none), in4 legato (a key was still held at note-on), in5 trig.
#pragma once
#include <math.h>
#include <string.h>
#include <stdint.h>

// name, default. Continuous params are 0..1; switches: oct_range 0..3 (16' 8' 4' 2'), pwm_src 0..2
// (LFO / MAN / ENV), sub_type 0..2 (-1 oct square / -2 oct square / -2 oct 25 % pulse), vca_mode 0..1
// (ENV / GATE), lfo_wave 0..3 (tri / square / S&H / smooth random), porta_mode 0..2 (off / auto / on).
#define PARAMS(X) \
    X(oct_range, 1) X(lfo_vco, 0) X(pw, 0) X(pwm_src, 1) \
    X(mix_pulse, 0) X(mix_saw, 1) X(mix_sub, 0.7) X(mix_noise, 0) X(sub_type, 0) \
    X(cutoff, 0.3) X(resonance, 0.55) X(env_vcf, 0.55) X(lfo_vcf, 0) X(kybd, 0.3) \
    X(vca_mode, 0) X(attack, 0) X(decay, 0.55) X(sustain, 0) X(release, 0.15) \
    X(lfo_wave, 0) X(lfo_rate, 0.5) X(porta, 0.3) X(porta_mode, 1) X(tuning, 0.5) X(volume, 0.8)

typedef struct {
#define X(n, d) double n;
    PARAMS(X)
#undef X
} Params;

static inline void params_default(Params *p) {
#define X(n, d) p->n = d;
    PARAMS(X)
#undef X
}
static inline double *param_field(Params *p, const char *name) {
#define X(n, d) if (!strcmp(name, #n)) return &p->n;
    PARAMS(X)
#undef X
    return NULL;
}

typedef struct {
    double ph, ph_sub, pitch_s, trig_s, adsr_lvl, stage, vca, cut_s;
    double lfo_ph, lfo_sh, lfo_sh_prev;
    double s1, s2, s3, s4, fb, hp_x, hp_y;
    uint32_t rng;
} Voice;

static inline void voice_init(Voice *v) { memset(v, 0, sizeof *v); v->pitch_s = 60; v->cut_s = 0.3; v->rng = 0x9E3779B9u; }

static inline double rnd(uint32_t *s) {            // white noise -1..1 (gen~: noise())
    uint32_t x = *s; x ^= x << 13; x ^= x >> 17; x ^= x << 5; *s = x;
    return (x / 2147483648.0) - 1.0;
}
static inline double polyblep(double t, double dt) {
    double r = 0.0;
    if (t < dt) { double tt = t / dt; r = tt + tt - tt * tt - 1.0; }
    else if (t > 1.0 - dt) { double tt = (t - 1.0) / dt; r = tt * tt + tt + tt + 1.0; }
    return r;
}
static inline double bsaw(double p, double dt) { return 2.0 * p - 1.0 - polyblep(p, dt); }
static inline double fracp(double x) { return x - floor(x); }
static inline double clampd(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }

static inline double voice_tick(Voice *v, double in1, double in2, double in3, double in4, double in5,
                                const Params *P, double sr, double *led) {
    (void)in3;
    const double T = 1.0 / sr;
    // ---- read state ----
    double ph = v->ph, ph_sub = v->ph_sub, pitch_s = v->pitch_s, trig_s = v->trig_s;
    double adsr = v->adsr_lvl, stage = v->stage, vca = v->vca, cut_s = v->cut_s;
    double lfo_ph = v->lfo_ph, lfo_sh = v->lfo_sh, lfo_sh_prev = v->lfo_sh_prev;
    double s1 = v->s1, s2 = v->s2, s3 = v->s3, s4 = v->s4, fb = v->fb, hp_x = v->hp_x, hp_y = v->hp_y;

    // ---- note events (js does the mono logic; trig counts note-ons; every note retriggers) ----
    double newtrig = (in5 != trig_s) ? 1 : 0;
    double legato = (in4 > 0.5) ? 1 : 0;
    double gateon = (in2 > 0.5) ? 1 : 0;

    // ---- LFO: free running 0.1 .. 30 Hz; tri / square / S&H / smooth random ----
    double lfo_hz = 0.1 * pow(300, P->lfo_rate);
    double lp = lfo_ph + lfo_hz * T;
    double lfo_new = (lp >= 1) ? 1 : 0;
    lp = lp - floor(lp);
    double n1 = rnd(&v->rng);
    double sh_prev = (lfo_new > 0.5) ? lfo_sh : lfo_sh_prev;
    double sh = (lfo_new > 0.5) ? n1 : lfo_sh;
    double tri = (lp < 0.5) ? (4 * lp - 1) : (3 - 4 * lp);
    double sqr = (lp < 0.5) ? 1 : -1;
    double smooth = sh_prev + (sh - sh_prev) * lp;
    double lfo = (P->lfo_wave < 0.5) ? tri : (P->lfo_wave < 1.5) ? sqr : (P->lfo_wave < 2.5) ? sh : smooth;

    // ---- portamento: off / auto (legato only) / on; RC glide 5 ms .. 2 s ----
    double porta_on = (P->porta_mode > 1.5) ? 1 : ((P->porta_mode > 0.5) ? legato : 0);
    double tau_p = 0.005 * pow(400, P->porta);
    double glided = pitch_s + (in1 - pitch_s) * (1 - exp(-T / tau_p));
    double pitch = (porta_on > 0.5) ? glided : in1;
    double semis = pitch + (P->tuning - 0.5) * 24 + (P->oct_range - 1) * 12 + lfo * 7 * P->lfo_vco * P->lfo_vco;
    double freq = 440 * pow(2, (semis - 69) / 12);
    double inc = clampd(freq * T, 0, 0.45);

    // ---- VCO: saw, pulse (PWM from LFO / manual / env), sub, noise; linear mixer ----
    double p = fracp(ph + inc);
    double saw = bsaw(p, inc);
    double pwv = (P->pwm_src < 0.5) ? 0.5 - 0.45 * P->pw * (0.5 + 0.5 * lfo)
               : (P->pwm_src < 1.5) ? 0.5 - 0.45 * P->pw
               : 0.5 - 0.45 * P->pw * adsr;
    pwv = clampd(pwv, 0.05, 0.95);
    double pls = saw - bsaw(fracp(p + pwv), inc);
    double sinc = (P->sub_type < 0.5) ? inc * 0.5 : inc * 0.25;
    double q = fracp(ph_sub + sinc);
    double subosc = bsaw(q, sinc) - bsaw(fracp(q + ((P->sub_type > 1.5) ? 0.25 : 0.5)), sinc);
    double nz = rnd(&v->rng);
    double osc = P->mix_saw * saw + P->mix_pulse * pls + P->mix_sub * subosc + P->mix_noise * nz * 0.7;

    // ---- ADSR: A 1.5 ms .. 3 s, D/R 2 ms .. 12 s, exponential segments ----
    double ta = 0.0015 * pow(2000, P->attack);
    double td = 0.002 * pow(6000, P->decay);
    double tr = 0.002 * pow(6000, P->release);
    double st = (newtrig > 0.5) ? 1 : stage;
    st = ((gateon < 0.5) && (st > 0.5)) ? 3 : st;
    double e = adsr;
    if (st == 1) { e = e + (1.3 - e) * (1 - exp(-T / ta)); if (e >= 1) { e = 1; st = 2; } }
    else if (st == 2) { e = e + (P->sustain - e) * (1 - exp(-T / td)); }
    else if (st == 3) { e = e + (0 - e) * (1 - exp(-T / tr)); if (e < 0.0001) { e = 0; st = 0; } }

    // ---- VCA: envelope or gate, 2 ms smoothing ----
    double vt = (P->vca_mode > 0.5) ? gateon : e;
    double va = vca + (vt - vca) * (1 - exp(-T / 0.002));

    // ---- VCF: IR3109-style 4-pole OTA cascade, tanh per stage, 2x oversampled, half-sample feedback ----
    double cs = cut_s + (P->cutoff - cut_s) * (1 - exp(-T / 0.005));
    double cut_hz = 20 * pow(2, cs * 9.5);
    double env_oct = e * P->env_vcf * 8 + lfo * P->lfo_vcf * 3 + (pitch - 60) / 12 * P->kybd;
    double fc = clampd(cut_hz * pow(2, env_oct), 10, 18000);
    double k = P->resonance * 4.3;
    double g = 1 - exp(-6.283185307179586 * fc / (sr * 2));
    double a1 = s1, a2 = s2, a3 = s3, a4 = s4, f = fb, y = 0;
    for (int i = 0; i < 2; i++) {
        double u = tanh(osc * 0.5 - k * f);
        double t1 = tanh(a1), t2 = tanh(a2), t3 = tanh(a3), t4 = tanh(a4);
        a1 = a1 + g * (u - t1);
        a2 = a2 + g * (t1 - t2);
        a3 = a3 + g * (t2 - t3);
        double a4n = a4 + g * (t3 - t4);
        f = 0.5 * (a4n + a4);
        a4 = a4n;
        y = a4;
    }

    // ---- output: resonance make-up, soft clip, DC block, VCA, volume ----
    y = tanh(y * (1 + 0.25 * k));
    double hp = y - hp_x + 0.997 * hp_y;
    double sig = hp * va * P->volume * P->volume;

    // ---- write state ----
    v->ph = p; v->ph_sub = q; v->pitch_s = pitch; v->trig_s = in5; v->adsr_lvl = e; v->stage = st;
    v->vca = va; v->cut_s = cs; v->lfo_ph = lp; v->lfo_sh = sh; v->lfo_sh_prev = sh_prev;
    v->s1 = a1; v->s2 = a2; v->s3 = a3; v->s4 = a4; v->fb = f; v->hp_x = y; v->hp_y = hp;
    *led = gateon;
    return sig;
}
