/*
 * mouthflow.js — Node for Max glue for the Mouthflow device.
 *
 * Bridges the device UI to the existing `mouthflow` CLI. It does NOT
 * re-implement the pipeline; it spawns `uv run mouthflow ...` (the same
 * verified record -> transcribe -> plan -> apply path) and streams progress
 * back to the patch.
 *
 * Inlet messages (wire patch -> [node.script mouthflow.js]):
 *   repo <path>            set the mouthflow repo dir (has pyproject + .env)
 *   uv <path>              set the absolute path to the uv binary
 *   device <id>            which voice: drums | bass | lead | drone | auto
 *   duration <seconds>     recording length for the next take
 *   hint <text...>         free-text planner hint (joined into one string)
 *   tempo <bpm>            force tempo in BPM (0 = auto-detect)
 *   countin <seconds>      count-in before recording, run CLI-side so the
 *                          "go" tick matches the moment capture opens (0 = off)
 *   list_kits              query Live, populate the kit menu
 *   kit_index <i>          choose kit by menu index (resolved to its URI)
 *   kit_uri <uri>          choose kit by explicit URI ("" = let planner pick)
 *   list_inputs            query input devices, populate the input menu
 *   input_index <i>        choose input device by menu index (0 = default)
 *   input <i>              choose input device by index ("" = default)
 *   file <path>            transcribe an existing audio file (explicit path)
 *   transcribe_clip        transcribe the SELECTED Live clip (path via bridge)
 *   generate               run a full take (record -> apply to Live)
 *   bars <auto|off|1|2|4|8|16> fit the clip to a whole bar count (loops on grid)
 *   correct <0|1>          note correction (scale snap) off/on
 *   key <C|F#|Bb|...>      force the key for correction ("" = auto-detect)
 *   scale <major|minor|..> force the scale for correction ("" = auto)
 *   record_start           begin an open-ended mic recording
 *   record_stop            finish recording -> transcribe + apply
 *   record <0|1>           toggle form of record_start/record_stop
 *   cancel                 kill an in-flight run
 *
 * Outlet messages ([node.script] -> patch):
 *   status <text>          human-readable progress line (route to a comment)
 *   level <dB>             live input level in dBFS while record-streaming
 *                          (route to a meter / number box)
 *   busy <0|1>             1 while a run is in flight (gate the button)
 *   tempo <bpm>            detected tempo from the last take
 *   rationale <text>       the planner's one-line reasoning
 *   done <0|1>             1 on success, 0 on failure
 *   error <text>           failure detail
 *   kitmenu clear | append <name>   populate a umenu/live.menu
 *   inputmenu clear | append <name> populate the input-device menu
 */

const Max = require("max-api");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const state = {
  repo: path.join(os.homedir(), "UhnTiss", "uhntiss"),
  uv: path.join(os.homedir(), ".local", "bin", "uv"),
  device: "drums", // which voice; per-voice panels load a copy with this baked
  duration: 8,
  hint: "",
  tempo: 0,
  kitUri: "",
  input: null, // input device index for record (null = system default)
  countin: 3,
  bars: "auto", // fit clip to bars: auto | off | 4 | 8 | 16
  correct: 1, // note correction (scale snap) on/off
  key: "", // force key for correction (e.g. "C", "F#"); "" = auto-detect
  scale: "", // force scale (major|minor|...); "" = auto
};

// The pitched note-correction + bar-fit flags shared by every pipeline call.
function correctionFlags() {
  const a = [];
  if (state.bars) a.push("--bars", String(state.bars));
  if (!Number(state.correct)) a.push("--no-correct");
  if (state.key) a.push("--key", String(state.key));
  if (state.scale) a.push("--scale", String(state.scale));
  return a;
}

let kits = []; // cached [{name, uri}] from list_kits
let inputs = []; // cached [{index, name}] from list_inputs
let child = null; // in-flight process
let inFlight = false; // a run is initiated
let recording = false; // an open-ended record-stream is actively capturing (pre-stop)
let cancelled = false; // the user killed the run; suppress the failure report

function status(msg) {
  Max.outlet("status", String(msg));
  Max.post(`[mouthflow] ${msg}`);
}

// uv's install location varies (curl installer vs Homebrew vs pipx). Resolve
// the configured path first, then the usual suspects, then bare "uv" (PATH).
function resolveUv() {
  const candidates = [
    state.uv,
    path.join(os.homedir(), ".local", "bin", "uv"),
    "/opt/homebrew/bin/uv",
    "/usr/local/bin/uv",
  ];
  for (let i = 0; i < candidates.length; i++) {
    try {
      if (candidates[i] && fs.existsSync(candidates[i])) return candidates[i];
    } catch (_) {
      /* keep looking */
    }
  }
  return "uv";
}

// Pull ANTHROPIC_API_KEY out of the repo's gitignored .env without needing a
// login shell. Falls back to the inherited environment.
function loadApiKey(repo) {
  try {
    const txt = fs.readFileSync(path.join(repo, ".env"), "utf8");
    const m = txt.match(/ANTHROPIC_API_KEY\s*=\s*["']?([^"'\r\n]+)/);
    if (m) return m[1].trim();
  } catch (_) {
    /* no .env — fall through */
  }
  return process.env.ANTHROPIC_API_KEY || "";
}

function runCli(args, { onStdout, onLine, onDone }) {
  const key = loadApiKey(state.repo);
  const env = Object.assign({}, process.env);
  if (key) env.ANTHROPIC_API_KEY = key;

  let proc;
  try {
    proc = spawn(resolveUv(), ["run", "mouthflow", ...args], {
      cwd: state.repo,
      env,
    });
  } catch (e) {
    onDone(-1, "", `spawn failed: ${e.message}`);
    return null;
  }

  let out = "";
  let errTail = "";
  proc.stdout.on("data", (d) => {
    out += d.toString();
    if (onStdout) onStdout(d.toString());
  });
  proc.stderr.on("data", (d) => {
    const text = d.toString();
    errTail = text;
    text.split(/\r?\n/).forEach((line) => {
      const clean = line.replace(/^\[mouthflow\]\s*/, "").trim();
      if (clean && onLine) onLine(clean);
    });
  });
  proc.on("error", (e) => onDone(-1, out, e.message));
  proc.on("close", (code) => onDone(code, out, errTail));
  return proc;
}

// Shared completion handler for any pipeline run (record or file transcribe).
function onPipelineDone(code, stdout, errTail) {
  child = null;
  inFlight = false;
  recording = false;
  Max.outlet("busy", 0);
  if (cancelled) {
    // The user killed it; "cancelled" was already shown — a SIGTERM exit
    // (code null) must not masquerade as a failure.
    cancelled = false;
    return;
  }
  if (code === 0) {
    try {
      const plan = JSON.parse(stdout.slice(stdout.indexOf("{")));
      if (plan.tempo) Max.outlet("tempo", plan.tempo);
      if (plan.rationale) Max.outlet("rationale", plan.rationale);
      status("done — clip applied to Live");
      Max.outlet("done", 1);
    } catch (e) {
      status("done, but could not parse plan");
      Max.outlet("done", 1);
    }
  } else {
    status(`failed (exit ${code}): ${errTail || "see Max console"}`);
    Max.outlet("error", errTail || `exit ${code}`);
    Max.outlet("done", 0);
  }
}

function generate() {
  if (child || inFlight) {
    status("a take is already running — cancel it first");
    return;
  }
  inFlight = true;
  const args = ["record", "--duration", String(state.duration), "--device", state.device, "--json"];
  if (state.input != null) args.push("--input", String(state.input));
  if (state.hint) args.push("--hint", state.hint);
  if (state.tempo) args.push("--tempo", String(state.tempo));
  if (state.kitUri) args.push("--instruments", state.kitUri);
  // The count-in runs CLI-side (--countin): its ticks are emitted right
  // before capture actually opens, so "REC — go!" means the mic is live. A
  // JS setTimeout count-in fired ~0.5s early (uv + import cost) and the
  // take's first hit got clipped.
  const n = Math.max(0, Math.floor(state.countin));
  if (n > 0) args.push("--countin", String(n));
  args.push(...correctionFlags());

  Max.outlet("busy", 1);
  status(n > 0 ? "get ready…" : "starting…");
  child = runCli(args, { onLine: (line) => status(line), onDone: onPipelineDone });
}

// Transcribe an existing audio FILE (the selected Live clip's sample) instead
// of recording the mic. The patch supplies the path via Live's API.
function transcribeFile(path) {
  if (!path) {
    status("no clip path — select an audio clip in Live first");
    return;
  }
  if (child || inFlight) {
    status("busy — cancel first");
    return;
  }
  inFlight = true;
  const args = ["run", path, "--device", state.device, "--json"];
  if (state.hint) args.push("--hint", state.hint);
  if (state.kitUri) args.push("--instruments", state.kitUri);
  args.push(...correctionFlags());
  Max.outlet("busy", 1);
  status(`transcribing clip (${state.device})…`);
  child = runCli(args, { onLine: (line) => status(line), onDone: onPipelineDone });
}

// Transcribe the clip currently SELECTED in Live (path fetched by the CLI from
// the forked bridge). One button, no path wrangling in the patch.
function transcribeClip() {
  if (child || inFlight) {
    status("busy — cancel first");
    return;
  }
  inFlight = true;
  const args = ["transcribe-clip", "--device", state.device, "--json"];
  if (state.hint) args.push("--hint", state.hint);
  if (state.kitUri) args.push("--instruments", state.kitUri);
  args.push(...correctionFlags());
  Max.outlet("busy", 1);
  status(`transcribing selected clip (${state.device})…`);
  child = runCli(args, { onLine: (line) => status(line), onDone: onPipelineDone });
}

// Start/stop recording: spawn `record-stream` (open-ended mic capture) on start;
// write "stop" to its stdin on stop, which finishes the take and transcribes.
// Replaces the fixed-duration timer with a performer-controlled length.
function recordStart() {
  if (child || inFlight) {
    status("busy — stop or cancel the current take first");
    return;
  }
  inFlight = true;
  recording = true;
  const args = ["record-stream", "--device", state.device, "--json"];
  if (state.input != null) args.push("--input", String(state.input));
  if (state.hint) args.push("--hint", state.hint);
  if (state.tempo) args.push("--tempo", String(state.tempo));
  if (state.kitUri) args.push("--instruments", state.kitUri);
  args.push(...correctionFlags());
  Max.outlet("busy", 1);
  status("● recording — hit Stop when done");
  child = runCli(args, {
    onLine: (line) => {
      // "level -23.4" lines are the live input meter — route them to their
      // own outlet so they don't overwrite the status comment.
      if (line.indexOf("level ") === 0) {
        const db = parseFloat(line.slice(6));
        if (!isNaN(db)) Max.outlet("level", db);
        return;
      }
      status(line);
    },
    onDone: onPipelineDone,
  });
}

// Stop is only meaningful while actively recording. Once stopped we're in the
// transcribe phase — further stops / toggle-bounces are ignored so they can't
// abort the in-flight pipeline. Killing only happens via the cancel handler.
function recordStop() {
  if (!recording || !child) {
    status(inFlight ? "finishing…" : "not recording");
    return;
  }
  recording = false; // -> transcribe phase; further record_stop is a no-op
  if (child.stdin && child.stdin.writable) {
    status("■ stopped — transcribing…");
    try {
      child.stdin.write("stop\n");
    } catch (e) {
      status("stop failed — use Cancel");
    }
  } else {
    status("stop failed — use Cancel");
  }
}

function listInputs() {
  runCli(["input-devices"], {
    onLine: () => {},
    onDone: (code, stdout) => {
      if (code !== 0) {
        status("could not list input devices");
        return;
      }
      try {
        inputs = JSON.parse(stdout.trim());
      } catch (e) {
        status("could not parse input list");
        return;
      }
      Max.outlet("inputmenu", "clear");
      Max.outlet("inputmenu", "append", "(default)");
      inputs.forEach((d) => Max.outlet("inputmenu", "append", d.name));
      status(`${inputs.length} input device(s)`);
    },
  });
}

function listKits() {
  status(`querying Live for ${state.device} instruments…`);
  runCli(["list-kits", "--device", state.device], {
    onLine: () => {}, // suppress the FAIL/log lines here; handled on close
    onDone: (code, stdout, errTail) => {
      if (code !== 0) {
        status(`kit query failed: ${errTail || `exit ${code}`}`);
        Max.outlet("error", errTail || `exit ${code}`);
        return;
      }
      try {
        kits = JSON.parse(stdout.trim());
      } catch (e) {
        status("could not parse kit list");
        return;
      }
      Max.outlet("kitmenu", "clear");
      Max.outlet("kitmenu", "append", "(let planner choose)");
      kits.forEach((k) => Max.outlet("kitmenu", "append", k.name));
      status(`${kits.length} kits available`);
    },
  });
}

Max.addHandler("repo", (...a) => (state.repo = a.join(" ")));
Max.addHandler("uv", (...a) => (state.uv = a.join(" ")));
Max.addHandler("device", (d) => (state.device = String(d).trim() || state.device));
Max.addHandler("duration", (d) => (state.duration = Number(d) || state.duration));
Max.addHandler("countin", (n) => (state.countin = Number(n) || 0));
Max.addHandler("tempo", (n) => (state.tempo = Number(n) || 0));
Max.addHandler("hint", (...a) => (state.hint = a.join(" ").trim()));
Max.addHandler("kit_uri", (...a) => (state.kitUri = a.join(" ").trim()));
Max.addHandler("kit_index", (i) => {
  const idx = Number(i);
  // index 0 is the "(let planner choose)" sentinel -> empty = planner picks
  state.kitUri = idx >= 1 && kits[idx - 1] ? kits[idx - 1].uri : "";
});
Max.addHandler("list_kits", listKits);
Max.addHandler("list_inputs", listInputs);
// raw input device index ("" / "default" -> system default)
Max.addHandler("input", (...a) => {
  const s = a.join(" ").trim();
  state.input = s === "" || s.toLowerCase() === "default" ? null : Number(s);
});
// menu selection: index 0 is the "(default)" sentinel
Max.addHandler("input_index", (i) => {
  const idx = Number(i);
  state.input = idx >= 1 && inputs[idx - 1] ? inputs[idx - 1].index : null;
});
// transcribe the selected clip's audio file (path from the patch's Live API)
Max.addHandler("file", (...a) => transcribeFile(a.join(" ").trim()));
// transcribe the selected clip (CLI fetches its path from the forked bridge)
Max.addHandler("transcribe_clip", transcribeClip);
Max.addHandler("generate", generate);
// pitched note-correction + bar-fit controls
Max.addHandler("bars", (...a) => {
  // clamp the free-text field so a typo can't reach the CLI / raise downstream
  const v = a.join(" ").trim().toLowerCase();
  state.bars = ["auto", "off", "1", "2", "4", "8", "16"].includes(v) ? v : "auto";
});
Max.addHandler("correct", (v) => (state.correct = Number(v) ? 1 : 0));
Max.addHandler("key", (...a) => (state.key = a.join(" ").trim()));
Max.addHandler("scale", (...a) => (state.scale = a.join(" ").trim()));
// start/stop recording (performer-controlled length)
Max.addHandler("record_start", recordStart);
Max.addHandler("record_stop", recordStop);
// a single toggle: 1 -> start, 0 -> stop
Max.addHandler("record", (v) => (Number(v) ? recordStart() : recordStop()));
Max.addHandler("cancel", () => {
  if (child) {
    cancelled = true; // onPipelineDone must not report the kill as a failure
    child.kill();
    child = null;
  }
  if (inFlight || recording) {
    inFlight = false;
    recording = false;
    Max.outlet("busy", 0);
    status("cancelled");
  }
});

status("mouthflow drums ready · 56d9e19");
