"""Actual Engine comparison: personal rejection, fresh-source grooves and rolls.

Both arms fit the same approved calibration only. Feature 3 is the paired
legacy ablation, not a retrained factory model. Reserved uptempo audio and
all its derivatives never reach fitting; roll attacks are removed before
building the separate roll profile. A stage verdict must include the failures.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np

from eval.real_auto import score
from eval.native_auto import match
from mouthflow import pocket

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval/data/pocket-reliability-sources.json"
RATE = 48000


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_profile(doc):
    """Exact feature-3 bank: full negatives only, original wire layout."""
    old = copy.deepcopy(doc)
    values = old["values"]
    at = 246 + int(values[2]) * pocket.VOICE_VALUES + int(values[3]) * 17
    blocks = np.asarray(values[at:]).reshape(-1, 31)
    blocks = blocks[blocks[:, 0] == 7]
    old["values"] = values[:at] + blocks.ravel().tolist()
    old["values"][1] = old["feature_version"] = 3
    old["values"][5] = old["commit_reject_examples"] = len(blocks)
    return old


def acoustic_score(rows, take):
    data = rows[:, :3].copy()
    notes = {36: 0, 38: 1, 42: 2, 46: 2}
    data[:, 1] = [notes[int(n)] for n in data[:, 1]]
    return score(data, take)


def brief(result):
    return {
        "correct": result["correct"],
        **{k: len(result[k]) for k in ("missed", "wrong", "extra")},
    }


def compare(binary, doc, y, take):
    arms = {}
    for name, profile in (("before", legacy_profile(doc)), ("after", doc)):
        rows = pocket.perform(binary, profile, y, RATE)
        live = rows["live"]
        if len(live):
            assert np.max(abs((live[:, 3] - live[:, 0]) * RATE - 479)) < 1e-5
        arms[name] = {
            "events": {k: v.tolist() for k, v in rows.items()},
            "scores": {
                lane: acoustic_score(events, take) for lane, events in rows.items()
            },
        }
    # A correct old hit must retain onset, note and velocity, not just count.
    retained = {}
    for lane in ("live", "stored"):
        old = np.asarray(arms["before"]["events"][lane]).reshape(-1, 4)
        new = np.asarray(arms["after"]["events"][lane]).reshape(-1, 4)
        preserved = 0
        for onset, note, velocity, _ in old:
            # Only measure exact retention here; acoustic errors are reported
            # separately and the report must not call removed noise a moved hit.
            candidates = new[abs(new[:, 0] - onset) <= 1.01 / RATE]
            preserved += int(
                any(
                    int(n) == int(note) and abs(v - velocity) < 1e-7
                    for _, n, v, _ in candidates
                )
            )
        correct_before = correct_retained = 0
        notes = {36: 0, 38: 1, 42: 2, 46: 2}
        for i, j in match(
            old[:, 0], [dict(t=e["time"]) for e in take["events"]], 0.025
        ):
            if notes[int(old[i, 1])] != take["events"][j]["drum"]:
                continue
            correct_before += 1
            close = new[abs(new[:, 0] - old[i, 0]) <= 1.01 / RATE]
            correct_retained += int(
                any(
                    notes[int(n)] == take["events"][j]["drum"]
                    and abs(v - old[i, 2]) < 1e-7
                    for _, n, v, _ in close
                )
            )
        assert correct_retained == correct_before, (
            "lost or moved a previously correct hit"
        )
        retained[lane] = dict(
            before=len(old),
            exact_retained=preserved,
            correct_before=correct_before,
            correct_retained=correct_retained,
        )
    return {"arms": arms, "retention": retained}


def run(binary: Path, manifest=MANIFEST):
    spec = json.loads(Path(manifest).read_text())
    data_dir = Path(manifest).parent
    analyzer = binary.with_name("pocket_analyze")
    sources = []

    def audio(entry):
        path = ROOT / entry["source"]
        assert digest(path) == entry["sha256"], f"changed source: {path}"
        sources.append(entry)
        return librosa.load(path, sr=RATE, mono=True)[0]

    voices, waves = [], {}
    for entry in spec["voices"]:
        y = audio(entry)
        waves[entry["source"]] = y
        rows = pocket.analyze(analyzer, y, RATE, policy="enrollment")
        voices.append(dict(name=entry["name"], note=entry["note"], rows=rows))
    audit = json.loads((data_dir / spec["drum_tests"]).read_text())
    # Source and derivative ownership are checked before any test is opened.
    training_sources = {v["source"] for v in spec["voices"]}
    assert not training_sources & {t["source"] for t in audit["takes"]}
    all_folds = []
    for fold in spec["negative_folds"]:
        assert fold["train"]["sha256"] != fold["test"]["sha256"], (
            "negative source leakage"
        )
        assert fold["train"]["source"] not in {t["source"] for t in audit["takes"]}
        noise = audio(fold["train"])
        negatives = pocket.analyze(analyzer, noise, RATE, policy="enrollment")
        doc = pocket.build_profile(voices, RATE, negatives)
        test = audio(fold["test"])
        noise_results = {}
        for name, profile in (("before", legacy_profile(doc)), ("after", doc)):
            replay = pocket.perform(binary, profile, test, RATE)
            noise_results[name] = {
                lane: dict(hits=len(rows), per_minute=len(rows) * 60 * RATE / len(test))
                for lane, rows in replay.items()
            }
        takes = []
        for take in audit["takes"]:
            y = audio(take)
            for gain in (1.0, 0.5, 0.25):
                result = compare(binary, doc, y * gain, take)
                takes.append(dict(name=take["name"], gain=gain, **result))
        all_folds.append(
            dict(
                train=fold["train"],
                test=fold["test"],
                noise=noise_results,
                takes=takes,
                profile_sources=[*spec["voices"], fold["train"]],
            )
        )
    # A separate profile excludes every fixed roll test attack before fitting.
    iso = json.loads((data_dir / spec["roll_tests"]).read_text())
    clean_voices, clips = [], {d: [] for d in range(3)}
    for entry, voice in zip(spec["voices"], voices):
        excluded = [e for e in iso["heldout_attacks"] if e["source"] == entry["source"]]
        rows = voice["rows"]
        keep = np.ones(len(rows), dtype=bool)
        for e in excluded:
            keep &= abs(rows[:, 0] - e["time"]) >= 0.2
            y = waves[entry["source"]]
            start = round((e["time"] - 0.008) * RATE)
            clip = y[start : start + round(0.110 * RATE)].copy()
            clip[:96] *= np.linspace(0, 1, 96)
            clip[-384:] *= np.linspace(1, 0, 384)
            clips[e["drum"]].append((clip, e["time"] - start / RATE))
        clean_voices.append({**voice, "rows": rows[keep]})
    # Use the first declared training fold, never choose a bank on test scores.
    negatives = pocket.analyze(
        analyzer, audio(spec["negative_folds"][0]["train"]), RATE, policy="enrollment"
    )
    roll_doc = pocket.build_profile(clean_voices, RATE, negatives)
    rolls = []
    pattern = [0, 2, 1, 0, 1, 2, 0, 2, 1, 1, 0, 2, 0, 1, 2, 0, 2, 1, 0, 1, 2, 0, 2, 1]
    for gap in (0.050, 0.075, 0.100, 0.140):
        for ghosts in (False, True):
            y = np.zeros(RATE * 4, np.float32)
            refs, used = [], [0, 0, 0]
            for i, drum in enumerate(pattern):
                clip, offset = clips[drum][used[drum] % len(clips[drum])]
                used[drum] += 1
                start = round((0.2 + i * gap) * RATE)
                y[start : start + len(clip)] += clip * (
                    0.25 if ghosts and i % 3 == 1 else 1.0
                )
                refs.append(dict(time=start / RATE + offset, drum=drum))
            result = compare(binary, roll_doc, y, dict(name="roll", events=refs))
            rolls.append(dict(gap_ms=gap * 1000, ghosts=ghosts, **result))
    roll_pass = all(
        case["arms"]["after"]["scores"][lane]["correct"] == 24
        and not case["arms"]["after"]["scores"][lane]["extra"]
        for case in rolls
        for lane in ("live", "stored")
    )
    # This protocol has no fresh human open-mic test. Synthetic stress and
    # humming alone must never turn into a stage qualification.
    fresh_open_mic_pass = None
    return dict(
        feature_version=pocket.FEATURE_VERSION,
        sample_rate=RATE,
        protocol="Calibration-only positives; one hum trains, the other tests. All complete mixed takes are evaluation only. Feature-3 paired runtime ablation. Roll attacks excluded before fitting the separate roll profile.",
        limits=spec["limits"],
        stage_ready=roll_pass and fresh_open_mic_pass is True,
        release_gates=dict(
            constructed_rolls=roll_pass, fresh_human_breath_speech=fresh_open_mic_pass
        ),
        stage_blocker="Dense roll stress still misses many attacks; fresh human breath/speech and expert rolls are unavailable.",
        binary_sha256=digest(binary),
        factory_sha256=digest(pocket.HEADER),
        sources=list({e["source"]: e for e in sources}.values()),
        folds=all_folds,
        rolls=rolls,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=pocket.BINARY.with_name("pocket_perform")
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = run(args.binary)
    if args.report:
        args.report.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    for fold in result["folds"]:
        print(fold["train"]["source"], "->", fold["test"]["source"], fold["noise"])
        for take in fold["takes"]:
            print(
                take["name"],
                take["gain"],
                {
                    a: {lane: brief(s) for lane, s in arm["scores"].items()}
                    for a, arm in take["arms"].items()
                },
            )
    for roll in result["rolls"]:
        print(
            "roll",
            roll["gap_ms"],
            roll["ghosts"],
            {
                a: {lane: brief(s) for lane, s in arm["scores"].items()}
                for a, arm in roll["arms"].items()
            },
        )
    print("Stage ready:", result["stage_ready"], result["stage_blocker"])


if __name__ == "__main__":
    main()
