"""Cross-performer numbers for the native Pocket classifier on AVP.

Every Pocket number so far is one performer (René; uptempo take held out).
This runs Pocket's own detector + forests (``pocket_analyze``, the device's
code) over the AVP dataset — 25 amateurs on a MacBook mic, 4,314 annotated
hits — and scores what the device would have sounded:

  onset recall     annotated hits that Pocket detected within 75 ms
  extra triggers   accepted detections with no annotated hit (per annotated hit)
  factory          class accuracy of the shipped forests on matched hits
                   (rep files + improvisations; every voice is unseen to Pocket)
  teach3-first     the device's Teach as shipped: the first 3 accepted hits of
                   each one-class repetition file become the profile; scored on
                   that performer's improvisation (Neighbours replica)
  teach3-medoid    what `mouthflow enroll export-pocket` does: the 3 medoids of
                   all repetition hits per voice
  profile30        hypothetical: up to 30 examples per voice in the same metric
                   (what a bigger Profile would buy — the engine stores 3)
  all123-knn       the Python classifier (all123 + k-NN enrollment on the SAME
                   Pocket-detected onsets) — apples to apples with teach3

Run: ``uv run python -m eval.avp_pocket [--sr 48000] [--gate 0.01] [--binary …]``
Needs the dataset (``eval/avp.py``) and the built tool (``mouthflow/pocket.py``).
"""

from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict

for _v in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "MKL_NUM_THREADS",
):
    os.environ.setdefault(_v, "4")

import numpy as np

from eval import avp
from mouthflow import pocket

TOL = 0.075  # Pocket's own matching tolerance (eval/native_auto.match)
LABEL_IDX = {"kick": 0, "snare": 1, "hat": 2}
MAX_PROFILE = 30


def _match(
    det_times: np.ndarray, gt_times: np.ndarray, tol: float = TOL
) -> list[tuple[int, int]]:
    """Greedy nearest one-to-one matching within ``tol`` (same rule as Pocket's eval)."""
    cands = sorted(
        (abs(t - g), i, j)
        for i, t in enumerate(det_times)
        for j, g in enumerate(gt_times)
        if abs(t - g) < tol
    )
    used_i, used_j, pairs = set(), set(), []
    for _, i, j in cands:
        if i not in used_i and j not in used_j:
            pairs.append((i, j))
            used_i.add(i)
            used_j.add(j)
    return pairs


def _acc(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    overall = float((y_true == y_pred).mean()) if len(y_true) else float("nan")
    per = {
        c: (
            float((y_pred[y_true == i] == i).mean())
            if (y_true == i).any()
            else float("nan")
        )
        for c, i in LABEL_IDX.items()
    }
    return overall, per


def _fmt(cell):
    o, per = cell
    return f"{o * 100:5.1f} (k{per['kick'] * 100:3.0f} s{per['snare'] * 100:3.0f} h{per['hat'] * 100:3.0f})"


def run(
    binary, sr: int, gate: float, modality: str = "Personal", want_all123: bool = True
) -> dict:
    import librosa

    scale = pocket.model_scale()
    rows = avp.load(modality)
    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_file[r["file"]].append(r)

    # per file: detections (accepted), matched pairs, and the all123 features at detection times
    t0 = time.time()
    files = {}
    n_gt = n_matched = n_extra = 0
    for f, frows in by_file.items():
        y = frows[0]["audio"]
        y_sr = frows[0]["sr"]
        if sr != y_sr:
            y_in = librosa.resample(y, orig_sr=y_sr, target_sr=sr)
        else:
            y_in = y
        det = pocket.accepted(pocket.analyze(binary, y_in, sr, gate))
        gt_t = np.array([r["t"] for r in frows])
        pairs = _match(det[:, 0], gt_t) if len(det) else []
        n_gt += len(frows)
        n_matched += len(pairs)
        n_extra += len(det) - len(pairs)
        files[f] = {
            "participant": frows[0]["participant"],
            "kind": frows[0]["kind"],
            "det": det,
            "pairs": pairs,
            "gt_label": np.array([LABEL_IDX[r["label"]] for r in frows]),
            "audio": y,
            "sr": y_sr,
        }
    print(f"analysed {len(files)} files at {sr} Hz in {time.time() - t0:.1f}s")
    print(
        f"onset recall {n_matched}/{n_gt} = {n_matched / n_gt:.3f}   extra triggers {n_extra} = {n_extra / n_gt:.2f} per annotated hit"
    )

    # factory: matched detections, shipped forest's drum vs annotation
    res = {}
    for kind in ("rep", "improv", "all"):
        yt, yp = [], []
        for fi in files.values():
            if kind != "all" and fi["kind"] != kind:
                continue
            for i, j in fi["pairs"]:
                yt.append(fi["gt_label"][j])
                yp.append(int(fi["det"][i, 1]))
        res[f"factory:{kind}"] = _acc(yt, yp)

    # per-participant Teach variants, scored on matched improvisation detections
    parts = sorted({fi["participant"] for fi in files.values()})
    variants = {"teach3-first": [], "teach3-medoid": [], f"profile{MAX_PROFILE}": []}
    truths = []
    all123_pred, all123_truth = [], []
    if want_all123:
        from mouthflow.devices.drum.features import extract
        from eval.avp_eval import KNN

    for p in parts:
        rep_files = [
            fi
            for fi in files.values()
            if fi["participant"] == p and fi["kind"] == "rep"
        ]
        imp_files = [
            fi
            for fi in files.values()
            if fi["participant"] == p and fi["kind"] == "improv"
        ]
        if not imp_files:
            continue
        # repetition hits per drum (matched detections whose annotation is that drum)
        by_drum: dict[int, list[np.ndarray]] = {0: [], 1: [], 2: []}
        first3: dict[int, list[np.ndarray]] = {0: [], 1: [], 2: []}
        rep_feats_123, rep_labels = [], []
        for fi in rep_files:
            for i, j in sorted(fi["pairs"]):
                d = int(fi["gt_label"][j])
                by_drum[d].append(fi["det"][i, 4:])
                if len(first3[d]) < 3:
                    first3[d].append(fi["det"][i, 4:])
                if want_all123:
                    rep_feats_123.append(
                        extract("all123", fi["audio"], fi["sr"], float(fi["det"][i, 0]))
                    )
                    rep_labels.append(d)
        if any(len(v) < 3 for v in by_drum.values()):
            continue
        medoids = pocket.pick_examples(
            {d: np.array(v) for d, v in by_drum.items()}, scale
        )
        profiles = {
            "teach3-first": [(ex, d) for d in range(3) for ex in first3[d]],
            "teach3-medoid": [(ex, d) for d in range(3) for ex in medoids[d]],
            f"profile{MAX_PROFILE}": [
                (ex, d) for d in range(3) for ex in by_drum[d][:MAX_PROFILE]
            ],
        }
        knn = (
            KNN().fit(
                np.array(rep_feats_123), np.array([avp.CLASSES[i] for i in rep_labels])
            )
            if want_all123
            else None
        )
        for fi in imp_files:
            for i, j in fi["pairs"]:
                feats = fi["det"][i, 4:]
                truths.append(int(fi["gt_label"][j]))
                for name, prof in profiles.items():
                    variants[name].append(pocket.vote(feats, prof, scale))
                if knn is not None:
                    pred = knn.predict(
                        np.array(
                            [
                                extract(
                                    "all123",
                                    fi["audio"],
                                    fi["sr"],
                                    float(fi["det"][i, 0]),
                                )
                            ]
                        )
                    )[0]
                    all123_pred.append(LABEL_IDX[pred])
                    all123_truth.append(int(fi["gt_label"][j]))
    for name, preds in variants.items():
        res[name] = _acc(truths, preds)
    if want_all123:
        res["all123-knn (same onsets)"] = _acc(all123_truth, all123_pred)
    res["_meta"] = {
        "n_gt": n_gt,
        "matched": n_matched,
        "extra": n_extra,
        "sr": sr,
        "gate": gate,
        "improv_scored": len(truths),
    }
    return res


def run_enrollment(binary, sr: int, gate: float, modality: str = "Personal") -> dict:
    """Native v5 banks from whole one-class clips; improvisations are test-only.

    Score every matched candidate, counting rejection as wrong. Also report
    accepted onset coverage/extras so accuracy cannot hide rejected quiet hits.
    Four-voice results are collapsed to K/S/H for the core comparison.
    """
    import subprocess
    import tempfile
    from pathlib import Path
    import librosa

    by_file = defaultdict(list)
    for r in avp.load(modality):
        by_file[r["file"]].append(r)
    files = []
    for path, source in by_file.items():
        first = source[0]
        y = first["audio"]
        if first["sr"] != sr:
            y = librosa.resample(y, orig_sr=first["sr"], target_sr=sr)
        rows = pocket.analyze(binary, y, sr, gate, policy="enrollment")
        files.append(
            dict(
                path=path,
                source=source,
                rows=rows,
                participant=first["participant"],
                kind=first["kind"],
            )
        )
    result = {}
    for four in (False, True):
        names = ["kick", "snare", "hat"] + (["hat_open"] if four else [])
        notes = [36, 38, 42, 46]
        truth, live, committed = [], [], []
        totals = dict(
            annotated=0,
            matched_candidates=0,
            accepted_matched=0,
            extra_accepted=0,
            rejected_matched=0,
            truncated=0,
            native_python_rows=0,
            participants=0,
        )
        for participant in sorted({f["participant"] for f in files}):
            train = {name: [] for name in names}
            tests = []
            for fi in files:
                if fi["participant"] != participant:
                    continue
                if fi["kind"] != "rep":
                    tests.append(fi)
                    continue
                first = fi["source"][0]
                name = (
                    "hat_open"
                    if four and first["raw_label"] == "hho"
                    else first["label"]
                )
                train[name].append(fi["rows"])
            voices = [
                {"name": name, "note": notes[d], "rows": np.concatenate(train[name])}
                for d, name in enumerate(names)
            ]
            # No matching/annotation filtering, query scaling or test-based
            # example selection: exactly the export-pocket clip protocol.
            doc = pocket.build_profile(voices, sr)
            totals["participants"] += 1
            with tempfile.TemporaryDirectory() as tmp:
                profile = Path(tmp) / "profile.txt"
                profile.write_text(" ".join(map(str, doc["values"])))
                for fi in tests:
                    rows, source = fi["rows"], fi["source"]
                    payload = "\n".join(" ".join(map(str, row[4:144])) for row in rows)
                    process = subprocess.run(
                        [str(Path(binary).with_name("pocket_profile")), str(profile)],
                        input=payload,
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    pred = np.array(
                        [
                            list(map(int, line.split(",")))
                            for line in process.stdout.splitlines()
                        ]
                    ).reshape(-1, 2)
                    replica = pocket.predict_profile(doc, rows)
                    if not np.array_equal(pred, replica):
                        raise AssertionError("native/Python profile metric mismatch")
                    totals["native_python_rows"] += len(rows)
                    pairs = _match(rows[:, 0], np.array([r["t"] for r in source]))
                    accepted_pairs = _match(
                        rows[pred[:, 0] >= 0, 0], np.array([r["t"] for r in source])
                    )
                    totals["annotated"] += len(source)
                    totals["matched_candidates"] += len(pairs)
                    totals["accepted_matched"] += len(accepted_pairs)
                    totals["extra_accepted"] += int((pred[:, 0] >= 0).sum()) - len(
                        accepted_pairs
                    )
                    totals["truncated"] += int((rows[:, 143] < round(0.040 * sr)).sum())
                    for i, j in pairs:
                        truth.append(LABEL_IDX[source[j]["label"]])
                        live.append(2 if pred[i, 0] == 3 else int(pred[i, 0]))
                        committed.append(2 if pred[i, 1] == 3 else int(pred[i, 1]))
                        totals["rejected_matched"] += int(pred[i, 0] < 0)
        name = "v5-4voices" if four else "v5-3voices"
        result[name + "-live10"] = _acc(truth, live)
        result[name + "-loop80"] = _acc(truth, committed)
        result[name + "-coverage"] = totals
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sr",
        type=int,
        default=44100,
        help="analysis rate (AVP is 44.1 k; Pocket Sets are often 48 k)",
    )
    ap.add_argument("--gate", type=float, default=0.010)
    ap.add_argument("--binary", default=None)
    ap.add_argument("--no-all123", action="store_true")
    ap.add_argument(
        "--enrollment",
        action="store_true",
        help="Also score native v5 banks from whole enrollment clips",
    )
    ap.add_argument(
        "--report", type=str, help="Write all tables and denominators as JSON"
    )
    ap.add_argument("--modality", default="Personal")
    args = ap.parse_args()
    if not avp.available():
        print(f"AVP dataset not found at {avp.AVP_DIR}; see eval/avp.py")
        return 2
    try:
        binary = pocket.find_binary(args.binary)
    except pocket.PocketError as exc:
        print(exc)
        return 2
    res = run(binary, args.sr, args.gate, args.modality, want_all123=not args.no_all123)
    all_results = dict(res)
    meta = res.pop("_meta")
    print(
        f"\nPocket on AVP {args.modality} — {meta['improv_scored']} matched improvisation hits scored for the Teach rows"
    )
    print(f"{'protocol':28s} {'acc (per class)':>24s}")
    for name, cell in res.items():
        print(f"{name:28s} {_fmt(cell):>24s}")
    if args.enrollment:
        enrolled = run_enrollment(binary, args.sr, args.gate, args.modality)
        all_results.update(enrolled)
        print(
            "\nNative v5: whole repetition clips -> improvisation; rejection counts as wrong; core K/S/H classes"
        )
        for name, cell in enrolled.items():
            if name.endswith("coverage"):
                print(name + ": " + str(cell))
            else:
                print(f"{name:28s} {_fmt(cell):>24s}")
    if args.report:
        import json
        from pathlib import Path

        Path(args.report).write_text(json.dumps(all_results, indent=2) + "\n")
    print(
        "\nReference, same dataset, annotated onsets (eval/avp_eval.py): all123 k-NN enrollment 83.4, "
        "5/class 80.0; mouthflow global MLP 68.0 leave-one-performer-out."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
