"""Personal native reject-class audit; one hum clip trains, the other tests.

No synthetic breath control or held-out drum take is used as enrollment.
The annotated drum fixtures are evaluation-only. Reports incremental loss
against the same positive profile, rather than hiding losses in accuracy.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import subprocess
import tempfile
from mouthflow import pocket

ROOT = Path(__file__).resolve().parents[1]


def run(binary: Path, sr=48000):
    import librosa

    def analyze(path):
        y, _ = librosa.load(path, sr=sr, mono=True)
        return pocket.analyze(binary, y, sr, policy="enrollment"), len(y) / sr

    voices = []
    for name, note, stem in (
        ("kick", 36, "kick"),
        ("snare", 38, "snare"),
        ("hat", 42, "hat_closed"),
        ("hat_open", 46, "hat_open"),
    ):
        rows, _ = analyze(ROOT / "calibration" / (stem + ".wav"))
        voices.append(dict(name=name, note=note, rows=rows))
    positive = pocket.build_profile(voices, sr)
    hums = [ROOT / "datasets/bass" / f"b0{i}.hum.wav" for i in (1, 2)]
    if not all(p.exists() for p in hums):
        raise pocket.PocketError(
            "copy b01.hum.wav and b02.hum.wav to datasets/bass (never commit audio)"
        )
    hrows = [analyze(p) for p in hums]
    from eval.avp_pocket import _match

    audit = json.loads((ROOT / "eval/data/pocket-real-takes.json").read_text())
    audited = {t["sha256"]: t["events"] for t in audit["takes"]}
    fixtures = []
    for name in ("01_boombap_mimic.wav", "02_bb100.wav"):
        rows, seconds = analyze(ROOT / "tests/fixtures/clips" / name)
        digest = hashlib.sha256(
            (ROOT / "tests/fixtures/clips" / name).read_bytes()
        ).hexdigest()
        events = audited[digest]
        fixtures.append((name, rows, seconds, events))
    parity_rows = 0

    def verified_predict(doc, rows):
        nonlocal parity_rows
        replica = pocket.predict_profile(doc, rows)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.txt"
            path.write_text(" ".join(map(str, doc["values"])))
            data = "\n".join(" ".join(map(str, row[4:144])) for row in rows)
            output = subprocess.run(
                [str(binary.with_name("pocket_profile")), str(path)],
                input=data,
                text=True,
                capture_output=True,
                check=True,
            )
            actual = np.array(
                [list(map(int, line.split(","))) for line in output.stdout.splitlines()]
            ).reshape(-1, 2)
        assert np.array_equal(actual, replica), "native/Python noise-gate mismatch"
        parity_rows += len(rows)
        return actual

    folds = []
    for train, test in ((0, 1), (1, 0)):
        doc = pocket.build_profile(voices, sr, hrows[train][0])
        rows, seconds = hrows[test]
        before = verified_predict(positive, rows)
        after = verified_predict(doc, rows)
        drums = []
        for name, row, duration, events in fixtures:
            a = verified_predict(positive, row)
            b = verified_predict(doc, row)
            pairs = _match(row[:, 0], np.array([e["time"] for e in events]), tol=0.025)
            matched_before = sum(a[i, 0] >= 0 for i, _ in pairs)
            matched_after = sum(b[i, 0] >= 0 for i, _ in pairs)
            lost = [
                dict(time=float(row[i, 0]), reference=events[j])
                for i, j in pairs
                if a[i, 0] >= 0 and b[i, 0] < 0
            ]
            committed_lost = [
                dict(time=float(row[i, 0]), reference=events[j])
                for i, j in pairs
                if a[i, 1] >= 0 and b[i, 1] < 0
            ]
            drums.append(
                dict(
                    committed_lost=committed_lost,
                    committed_matched_before=int(sum(a[i, 1] >= 0 for i, _ in pairs)),
                    committed_matched_after=int(sum(b[i, 1] >= 0 for i, _ in pairs)),
                    annotated=len(events),
                    matched_before=int(matched_before),
                    matched_after=int(matched_after),
                    lost=lost,
                    file=name,
                    candidates=len(row),
                    before=int((a[:, 0] >= 0).sum()),
                    after=int((b[:, 0] >= 0).sum()),
                    additional_rejections=int(((a[:, 0] >= 0) & (b[:, 0] < 0)).sum()),
                )
            )
        folds.append(
            dict(
                train=hums[train].name,
                test=hums[test].name,
                train_sha256=hashlib.sha256(hums[train].read_bytes()).hexdigest(),
                test_sha256=hashlib.sha256(hums[test].read_bytes()).hexdigest(),
                train_candidates=len(hrows[train][0]),
                retained=doc["reject_examples"],
                seconds=seconds,
                before=int((before[:, 0] >= 0).sum()),
                after=int((after[:, 0] >= 0).sum()),
                before_per_min=float((before[:, 0] >= 0).sum() * 60 / seconds),
                after_per_min=float((after[:, 0] >= 0).sum() * 60 / seconds),
                committed_after=int((after[:, 1] >= 0).sum()),
                committed_after_per_min=float((after[:, 1] >= 0).sum() * 60 / seconds),
                drums=drums,
            )
        )
    return dict(
        feature_version=pocket.FEATURE_VERSION,
        sr=sr,
        protocol="Positive enrollment: four isolated calibration clips. Negative enrollment: one hum; test: the other. No threshold fitted on test audio.",
        limits="Humming is not speech or breath. Additional rejections on drum fixtures include unannotated candidates; inspect matched-hit recall separately.",
        native_python_rows=parity_rows,
        folds=folds,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=pocket.BINARY)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run(pocket.find_binary(args.binary))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
