// Loads a Teach profile built offline and hands it to the engine.
// `mouthflow enroll export-pocket` writes uhntiss_pocket_profile.json into the
// Max user Library (on the search path), so a bare filename resolves. The
// file holds the v4 profile block: 3 x (count + 3 x 17 features) = 156 values.
// No audio, no loop mutation lives here; the engine validates the payload.
autowatch = 1;
inlets = 1;
outlets = 1;

var PROFILE_VALUES = 3 * (1 + 3 * 17);

function bang() {
  var f = new File("uhntiss_pocket_profile.json", "read");
  if (!f.isopen) {
    post("pocket: no profile found — run `mouthflow enroll export-pocket` first\n");
    return;
  }
  var text = f.readstring(f.eof);
  f.close();
  var doc = null;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    post("pocket: profile file is not valid JSON\n");
    return;
  }
  if (!doc || doc.version !== 4 || !doc.values || doc.values.length !== PROFILE_VALUES) {
    post("pocket: profile file has the wrong shape (expected version 4, " + PROFILE_VALUES + " values)\n");
    return;
  }
  var msg = ["profile"].concat(doc.values);
  outlet.apply(this, [0].concat(msg));
  post("pocket: profile loaded (" + (doc.sample_rate || "?") + " Hz, " + (doc.pick || "?") + ")\n");
}

// live.text in button mode sends 1 on press.
function msg_int(v) {
  if (v) bang();
}
msg_int.local = 1;
