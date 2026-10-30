// pocket303_notes.js — mono note logic for the Pocket 303 (Max `js` object).
// In:  "pitch velocity" lists from [midiparse] outlet 0.
// Out: one list per event:  note  held  accent  slide  trig
//   note   = MIDI note the voice should play
//   held   = 1 while any key is down (VCA gate)
//   accent = 1 when the note-on velocity is >= 100 (latched by the core at trig)
//   slide  = 1 when another note was still held at note-on (legato: no retrigger, glide)
//   trig   = counter that changes on every note-on, so off+on inside one vector still retriggers
inlets = 1;
outlets = 1;
var stack = [];   // held notes, oldest first (last-note priority)
var cur = 60;
var trig = 0;
var accentThreshold = 100;

function list(pitch, vel) {
    if (arguments.length < 2) return;
    pitch = Math.round(pitch);
    if (vel > 0) {
        var slide = stack.length > 0 ? 1 : 0;
        remove(pitch);
        stack.push(pitch);
        cur = pitch;
        trig = (trig + 1) % 1000000;
        outlet(0, [cur, 1, vel >= accentThreshold ? 1 : 0, slide, trig]);
    } else {
        remove(pitch);
        if (stack.length > 0) {
            if (pitch == cur) {           // top note released: glide back to the one still held
                cur = stack[stack.length - 1];
                outlet(0, [cur, 1, 0, 1, trig]);
            }
        } else {
            outlet(0, [cur, 0, 0, 0, trig]);
        }
    }
}
function remove(pitch) {
    for (var i = stack.length - 1; i >= 0; i--) if (stack[i] == pitch) stack.splice(i, 1);
}
function allnotesoff() { stack = []; outlet(0, [cur, 0, 0, 0, trig]); }
function reset() { allnotesoff(); }
function threshold(v) { accentThreshold = v; }
