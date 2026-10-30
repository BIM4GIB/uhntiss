// Mono note logic shared by the Pocket plug-ins: the same last-note-priority held stack the M4L js and the
// proto render hosts implement. Outputs are the five gen~ inputs: in1 note, in2 held, in3 accent (vel >= 100),
// in4 legato/slide (a key was still held at note-on), in5 trig counter.
#pragma once
#include <string.h>
typedef struct { int stack[32]; int n; double in1, in2, in3, in4, in5; } MonoNotes;
static inline void mono_init(MonoNotes *m) { memset(m, 0, sizeof *m); m->in1 = 60; }
static inline void mono_note_on(MonoNotes *m, int pitch, int vel) {
    if (m->n >= 32) return;
    m->in4 = (m->n > 0) ? 1 : 0; m->stack[m->n++] = pitch; m->in1 = pitch; m->in3 = (vel >= 100) ? 1 : 0; m->in2 = 1; m->in5 += 1;
}
static inline void mono_note_off(MonoNotes *m, int pitch) {
    int j; for (j = 0; j < m->n; j++) if (m->stack[j] == pitch) break;
    if (j < m->n) { for (; j < m->n - 1; j++) m->stack[j] = m->stack[j + 1]; m->n--; }
    if (m->n > 0) { if (pitch == (int)m->in1) { m->in1 = m->stack[m->n - 1]; m->in4 = 1; } }
    else m->in2 = 0;
}
static inline void mono_all_off(MonoNotes *m) { m->n = 0; m->in2 = 0; }
