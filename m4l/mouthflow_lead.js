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
 *   countin <seconds>      silent count-in before recording (0 = off)
 *   list_kits              query Live, populate the kit menu
 *   kit_index <i>          choose kit by menu index (resolved to its URI)
 *   kit_uri <uri>          choose kit by explicit URI ("" = let planner pick)
 *   list_inputs            query input devices, populate the input menu
 *   input_index <i>        choose input device by menu index (0 = default)
 *   input <i>              choose input device by index ("" = default)
 *   file <path>            transcribe an existing audio file (the selected clip)
 *   generate               run a full take (record -> apply to Live)
 *   cancel                 kill an in-flight run
 *
 * Outlet messages ([node.script] -> patch):
 *   status <text>          human-readable progress line (route to a comment)
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
  device: "lead", // which voice; per-voice panels load a copy with this baked
  duration: 8,
  hint: "",
  tempo: 0,
  kitUri: "",
  input: null, // input device index for record (null = system default)
  countin: 3,
};

let kits = []; // cached [{name, uri}] from list_kits
let inputs = []; // cached [{index, name}] from list_inputs
let child = null; // in-flight process

function status(msg) {
  Max.outlet("status", String(msg));
  Max.post(`[mouthflow] ${msg}`);
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
    proc = spawn(state.uv, ["run", "mouthflow", ...args], {
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
  Max.outlet("busy", 0);
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
  if (child) {
    status("a take is already running — cancel it first");
    return;
  }
  const args = ["record", "--duration", String(state.duration), "--device", state.device, "--json"];
  if (state.input != null) args.push("--input", String(state.input));
  if (state.hint) args.push("--hint", state.hint);
  if (state.tempo) args.push("--tempo", String(state.tempo));
  if (state.kitUri) args.push("--instruments", state.kitUri);

  Max.outlet("busy", 1);

  const launch = () => {
    status(`recording ${state.duration}s — beatbox now!`);
    child = runCli(args, { onLine: (line) => status(line), onDone: onPipelineDone });
  };

  // Silent count-in so the user knows when to start (the CLI records
  // immediately with no cue of its own).
  const n = Math.max(0, Math.floor(state.countin));
  if (n <= 0) return launch();
  let i = n;
  const tick = () => {
    if (i > 0) {
      status(`get ready… ${i}`);
      i -= 1;
      setTimeout(tick, 1000);
    } else {
      launch();
    }
  };
  tick();
}

// Transcribe an existing audio FILE (the selected Live clip's sample) instead
// of recording the mic. The patch supplies the path via Live's API.
function transcribeFile(path) {
  if (!path) {
    status("no clip path — select an audio clip in Live first");
    return;
  }
  if (child) {
    status("busy — cancel first");
    return;
  }
  const args = ["run", path, "--device", state.device, "--json"];
  if (state.hint) args.push("--hint", state.hint);
  if (state.kitUri) args.push("--instruments", state.kitUri);
  Max.outlet("busy", 1);
  status(`transcribing clip (${state.device})…`);
  child = runCli(args, { onLine: (line) => status(line), onDone: onPipelineDone });
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
Max.addHandler("generate", generate);
Max.addHandler("cancel", () => {
  if (child) {
    child.kill();
    child = null;
    Max.outlet("busy", 0);
    status("cancelled");
  }
});

status("mouthflow device ready");
