// File I/O only. Native validation + the audio thread acknowledge installation.
autowatch = 1;
inlets = 1;
outlets = 2;
function fail(message) { post("pocket: " + message + "\n"); outlet(1, "profile_error"); }
fail.local = 1;
function bang() {
  var f = new File("uhntiss_pocket_profile.json", "read");
  if (!f.isopen) { fail("export a profile with mouthflow enroll export-pocket first"); return; }
  // Max readstring returns at most 32767 characters while advancing by the
  // requested count. Stay below that limit so a one-line profile is not lost.
  if (f.eof > 4 * 1024 * 1024) { f.close(); fail("profile file is too large; export it again"); return; }
  var chunks = [];
  while (f.position < f.eof) {
    var before = f.position;
    chunks.push(f.readstring(Math.min(2048, f.eof - before)));
    if (f.position <= before) { f.close(); fail("could not finish reading profile"); return; }
  }
  f.close();
  var text = chunks.join("");
  var doc;
  try { doc = JSON.parse(text); } catch (e) { fail("invalid profile JSON (read " + text.length + " characters): " + e.message); return; }
  if (!doc || !(doc.values instanceof Array) ||
      !((doc.version === 4 && doc.values.length === 156) ||
        (doc.version === 5 && (doc.feature_version === 3 || doc.feature_version === 4) && doc.values[0] === 5 && doc.values[1] === doc.feature_version && doc.values.length <= 24726))) {
    fail("unsupported profile; export it again for Pocket 07.1"); return;
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
