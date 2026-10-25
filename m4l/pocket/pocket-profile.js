// File I/O only. Native validation + the audio thread acknowledge installation.
autowatch = 1;
inlets = 1;
outlets = 2;
function fail(message) { post("pocket: " + message + "\n"); outlet(1, "profile_error"); }
fail.local = 1;
function bang() {
  var f = new File("uhntiss_pocket_profile.json", "read");
  if (!f.isopen) { fail("export a profile with mouthflow enroll export-pocket first"); return; }
  var text = f.readstring(f.eof); f.close();
  var doc;
  try { doc = JSON.parse(text); } catch (e) { fail("invalid profile JSON"); return; }
  if (!doc || !(doc.values instanceof Array) ||
      !((doc.version === 4 && doc.values.length === 156) ||
        (doc.version === 5 && doc.feature_version === 3 && doc.values[0] === 5 && doc.values[1] === 3 && doc.values.length <= 24726))) {
    fail("unsupported profile; export it again for Pocket 06"); return;
  }
  for (var i = 0; i < doc.values.length; ++i) {
    if (typeof doc.values[i] !== "number" || !isFinite(doc.values[i])) {
      fail("non-finite profile value"); return;
    }
  }
  outlet.apply(this, [0, "profile"].concat(doc.values));
  post("pocket: profile sent; awaiting audio engine\n");
}
// Public: live.text sends an int. Marking this local made PROFILE inert.
function msg_int(v) { if (v) bang(); }
