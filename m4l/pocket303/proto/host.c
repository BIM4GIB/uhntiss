// Renders a script of params / notes / automation through the voice to a float WAV.
// Script lines:  R sr | T bpm | L beats | B blocksize | P name value | A beat name value |
//                N start_beat dur_beat pitch vel | O path
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "voice.h"

typedef struct { double t; double v; } APt;
typedef struct { char name[16]; APt pts[256]; int n; } Auto;
typedef struct { double on, off; int pitch, vel; } Note;

static double *pfield(Params *p, const char *n) {
    if (!strcmp(n,"cutoff")) return &p->cutoff; if (!strcmp(n,"resonance")) return &p->resonance;
    if (!strcmp(n,"envmod")) return &p->envmod; if (!strcmp(n,"decay")) return &p->decay;
    if (!strcmp(n,"accent")) return &p->accent; if (!strcmp(n,"tuning")) return &p->tuning;
    if (!strcmp(n,"slide")) return &p->slide; if (!strcmp(n,"volume")) return &p->volume;
    if (!strcmp(n,"wave")) return &p->wave; return NULL;
}
static double auto_at(const Auto *a, double beat) {
    if (a->n == 0) return 0; if (beat <= a->pts[0].t) return a->pts[0].v;
    for (int i = 1; i < a->n; i++) if (beat <= a->pts[i].t) {
        double f = (beat - a->pts[i-1].t) / (a->pts[i].t - a->pts[i-1].t);
        return a->pts[i-1].v + f * (a->pts[i].v - a->pts[i-1].v);
    }
    return a->pts[a->n-1].v;
}
static void wav_write(const char *path, const float *x, long n, int sr) {
    FILE *f = fopen(path, "wb"); if (!f) { perror(path); exit(1); }
    uint32_t data = n * 4; uint16_t fmt = 3, ch = 1, bits = 32; uint32_t br = sr * 4; uint16_t ba = 4;
    uint32_t riff = 36 + data, fmtlen = 16;
    fwrite("RIFF",1,4,f); fwrite(&riff,4,1,f); fwrite("WAVEfmt ",1,8,f); fwrite(&fmtlen,4,1,f);
    fwrite(&fmt,2,1,f); fwrite(&ch,2,1,f); fwrite(&sr,4,1,f); fwrite(&br,4,1,f); fwrite(&ba,2,1,f); fwrite(&bits,2,1,f);
    fwrite("data",1,4,f); fwrite(&data,4,1,f); fwrite(x,4,n,f); fclose(f);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: host script.txt\n"); return 1; }
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 1; }
    int sr = 44100, block = 64; double bpm = 136, length = 4; char out[1024] = "out.wav";
    Params p = {0.5,0.5,0.5,0.5,0.5,0.5,0.3,0.8,0};
    Auto autos[9]; int na = 0; Note notes[4096]; int nn = 0;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char k; if (sscanf(line, " %c", &k) != 1) continue;
        if (k == 'R') sscanf(line, "R %d", &sr);
        else if (k == 'T') sscanf(line, "T %lf", &bpm);
        else if (k == 'L') sscanf(line, "L %lf", &length);
        else if (k == 'B') sscanf(line, "B %d", &block);
        else if (k == 'O') sscanf(line, "O %1023s", out);
        else if (k == 'P') { char n[32]; double v; sscanf(line, "P %31s %lf", n, &v); double *d = pfield(&p, n); if (d) *d = v; }
        else if (k == 'A') { char n[32]; double t, v; sscanf(line, "A %lf %31s %lf", &t, n, &v);
            Auto *a = NULL; for (int i = 0; i < na; i++) if (!strcmp(autos[i].name, n)) a = &autos[i];
            if (!a) { a = &autos[na++]; strcpy(a->name, n); a->n = 0; }
            a->pts[a->n].t = t; a->pts[a->n].v = v; a->n++; }
        else if (k == 'N') { double s, d; int pi, ve; sscanf(line, "N %lf %lf %d %d", &s, &d, &pi, &ve);
            notes[nn].on = s; notes[nn].off = s + d; notes[nn].pitch = pi; notes[nn].vel = ve; nn++; }
    }
    fclose(f);
    double spb = 60.0 / bpm;                       // seconds per beat
    long total = (long)(length * spb * sr) + sr / 2;   // half a second of tail
    float *buf = calloc(total, sizeof(float));
    Voice v; voice_init(&v);
    // ---- mono note logic (what the Max-side js does): last-note priority, held stack ----
    int stack[32], sn = 0; double in1 = 60, in2 = 0, in3 = 0, in4 = 0, in5 = 0;
    // event list sorted by time (offs before ons at equal times)
    typedef struct { double t; int on; int pitch; int vel; } Ev; Ev *ev = malloc(sizeof(Ev) * nn * 2); int ne = 0;
    for (int i = 0; i < nn; i++) { ev[ne++] = (Ev){notes[i].on, 1, notes[i].pitch, notes[i].vel}; ev[ne++] = (Ev){notes[i].off, 0, notes[i].pitch, 0}; }
    for (int i = 0; i < ne; i++) for (int j = i + 1; j < ne; j++)
        if (ev[j].t < ev[i].t || (ev[j].t == ev[i].t && !ev[j].on && ev[i].on)) { Ev t = ev[i]; ev[i] = ev[j]; ev[j] = t; }
    int ei = 0;
    for (long s = 0; s < total; s += block) {
        double beat = (double)s / sr / spb;
        // control rate: automation + events falling in this block
        for (int i = 0; i < na; i++) { double *d = pfield(&p, autos[i].name); if (d) *d = auto_at(&autos[i], beat); }
        double block_end_beat = (double)(s + block) / sr / spb;
        while (ei < ne && ev[ei].t < block_end_beat) {
            Ev e = ev[ei++];
            if (e.on) { in4 = (sn > 0) ? 1 : 0; stack[sn++] = e.pitch; in1 = e.pitch; in3 = (e.vel >= 100) ? 1 : 0; in2 = 1; in5 += 1; }
            else { int j; for (j = 0; j < sn; j++) if (stack[j] == e.pitch) break;
                   if (j < sn) { for (; j < sn - 1; j++) stack[j] = stack[j+1]; sn--; }
                   if (sn > 0) { if (e.pitch == (int)in1) { in1 = stack[sn-1]; in4 = 1; } }
                   else in2 = 0; }
        }
        for (int i = 0; i < block && s + i < total; i++) {
            double led; buf[s + i] = (float)voice_tick(&v, in1, in2, in3, in4, in5, &p, sr, &led);
        }
    }
    wav_write(out, buf, total, sr);
    double pk = 0; for (long i = 0; i < total; i++) if (fabs(buf[i]) > pk) pk = fabs(buf[i]);
    printf("wrote %s  %.1fs  peak %.2f dBFS\n", out, (double)total / sr, 20 * log10(pk + 1e-9));
    return 0;
}
