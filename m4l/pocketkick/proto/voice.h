// Pocket Kick - C mirror of pocketkick.genexpr (bass drum, two engines). gen~ History discipline: read state
// once at the top, compute on locals, write once at the end. Inputs as the Pocket 303 (same js):
// in1 note, in2 held (unused: one-shot), in3 accent (vel >= 100), in4 legato (unused), in5 trig counter.
//   body 0 "Sine": sine with a 4 ms "knock" + an exponential pitch sweep, click + noise mixed in after it.
//   body 1 "303":  saw at the kick pitch into the 303's self-oscillating diode ladder whose cutoff makes the
//                  sweep; the click excites the ladder (the acid "303 kick" trick as its own engine).
// v0.2: the amp envelope (hold + decay) is split around the drive (sqrt before, sqrt after) so the attack
//       saturates while the tail decays exponentially; the 303 ladder restarts on every hit.
#pragma once
#include <math.h>
#include <string.h>
#include <stdint.h>

#define PARAMS(X) \
    X(body, 0) X(keytrack, 0) X(tune, 0.44) X(sweep, 0.5) X(sweep_dec, 0.4) \
    X(cutoff, 0.5) X(resonance, 0.7) X(hold, 0.3) X(decay, 0.25) X(click, 0.6) X(mix_noise, 0.15) X(noise_dec, 0.25) \
    X(drive, 0.55) X(tone, 0.8) X(accent, 0.5) X(volume, 0.8)

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
    double ph, trig_s, acc_note, tsince, penv, kenv, aenv, nenv, cenv;
    double s1, s2, s3, s4, fb, lp_tone, hp_x, hp_y;
    uint32_t rng;
} Voice;
static inline void voice_init(Voice *v) { memset(v, 0, sizeof *v); v->tsince = 10; v->rng = 0x9E3779B9u; }

static inline double rnd(uint32_t *s) {
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
    (void)in2; (void)in4;
    const double T = 1.0 / sr;
    double ph = v->ph, trig_s = v->trig_s, acc_note = v->acc_note, tsince = v->tsince;
    double penv = v->penv, kenv = v->kenv, aenv = v->aenv, nenv = v->nenv, cenv = v->cenv;
    double s1 = v->s1, s2 = v->s2, s3 = v->s3, s4 = v->s4, fb = v->fb, lp_tone = v->lp_tone, hp_x = v->hp_x, hp_y = v->hp_y;

    // ---- trigger (one-shot: note-off is ignored); accent latched at the trigger ----
    double newtrig = (in5 != trig_s) ? 1 : 0;
    double accn = (newtrig > 0.5) ? ((in3 > 0.5) ? 1 : 0) : acc_note;

    // ---- envelopes, all restart at the trigger ----
    double tau_p = 0.003 * pow(100, P->sweep_dec);     // pitch sweep 3 .. 300 ms
    double tau_a = 0.03 * pow(30, P->decay);           // amp decay 30 ms .. 900 ms (after the hold)
    double tau_n = 0.005 * pow(40, P->noise_dec);      // noise layer 5 .. 200 ms
    double hold_s = 0.06 * P->hold;                    // amp hold 0 .. 60 ms
    double tn = (newtrig > 0.5) ? 0 : tsince + T;
    double pe = ((newtrig > 0.5) ? 1 : penv) * exp(-T / tau_p);
    double ke = ((newtrig > 0.5) ? 1 : kenv) * exp(-T / 0.004);                  // the 4 ms knock
    double ae = (tn < hold_s) ? 1 : ((newtrig > 0.5) ? 1 : aenv) * exp(-T / tau_a);
    double ne = ((newtrig > 0.5) ? 1 : nenv) * exp(-T / tau_n);
    double ce = ((newtrig > 0.5) ? 1 : cenv) * exp(-T / 0.003);                  // 3 ms click burst

    // ---- pitch: TUNE 30 .. 120 Hz, optional key tracking from C1; sweep up to 5 oct + 3 oct x CLICK knock ----
    double semis = (P->keytrack > 0.5) ? (in1 - 36) : 0;
    double f0 = 30 * pow(2, 2 * P->tune) * pow(2, semis / 12);
    double sw_oct = P->sweep * 5 * pe + 3 * P->click * ke;
    double nz = rnd(&v->rng);
    double transient = ((newtrig > 0.5) ? 1 : 0) * P->click + nz * ce * P->click;

    // ---- body ----
    double a1 = s1, a2 = s2, a3 = s3, a4 = s4, f = fb, p = ph, bodysig = 0;
    if (P->body < 0.5) {
        double fosc = f0 * pow(2, sw_oct);
        p = (newtrig > 0.5) ? 0 : fracp(ph + fosc * T);
        bodysig = sin(6.283185307179586 * p) + transient;
    } else {
        if (newtrig > 0.5) { a1 = 0; a2 = 0; a3 = 0; a4 = 0; f = 0; }   // consistent hits: the ladder restarts
        double inc = f0 * T;
        p = (newtrig > 0.5) ? 0 : fracp(ph + inc);
        double saw = bsaw(p, inc);
        double fc = clampd(f0 * pow(2, (P->cutoff - 0.5) * 2 + sw_oct), 20, 12000);   // 0.5 = ring on the fundamental
        double k = 3.4 + P->resonance;
        double g = 1 - exp(-6.283185307179586 * fc / (sr * 2));
        for (int i = 0; i < 2; i++) {
            double u = saw * 0.8 + transient * 2 - k * f;
            double x = 2 * tanh(0.5 * u);
            a1 = a1 + g * (x - a1);
            a2 = a2 + g * (a1 - a2);
            a3 = a3 + g * (a2 - a3);
            double a4n = a4 + g * (a3 - a4);
            f = 0.5 * (a4n + a4);
            a4 = a4n;
        }
        bodysig = tanh(a4 * (1 + 0.25 * k)) * 1.3 + transient * 0.5;
    }
    // ---- half the amp envelope (sqrt) + accent BEFORE the drive: the attack saturates, the tail stays exponential ----
    double env_pre = sqrt(ae) * (1 + 0.5 * P->accent * accn);
    double mixed = (bodysig + nz * ne * P->mix_noise) * env_pre;

    // ---- drive (asymmetric tanh), tone lowpass, DC block, volume ----
    double drv = clampd(P->drive + 0.3 * P->accent * accn, 0, 1);
    double d = 1 + 9 * drv;
    double bias = 0.15 * drv;
    double driven = tanh(d * mixed + bias) - tanh(bias);
    double tf = 200 * pow(2, 6.6 * P->tone);
    double lt = lp_tone + (driven - lp_tone) * (1 - exp(-6.283185307179586 * tf * T));
    double hp = lt - hp_x + 0.9995 * hp_y;
    double sig = hp * sqrt(ae) * P->volume * P->volume * 0.8;   // the other half of the envelope

    v->ph = p; v->trig_s = in5; v->acc_note = accn; v->tsince = tn; v->penv = pe; v->kenv = ke; v->aenv = ae; v->nenv = ne; v->cenv = ce;
    v->s1 = a1; v->s2 = a2; v->s3 = a3; v->s4 = a4; v->fb = f; v->lp_tone = lt; v->hp_x = lt; v->hp_y = hp;
    *led = ae > 0.01 ? 1 : 0;
    return sig;
}
