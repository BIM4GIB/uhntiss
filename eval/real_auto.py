"""Full-take Auto research and native regression. Never imported by the device.

The acoustic audit is signal-assisted, not independent listening ground truth.
The Pocket trainer in transfer_auto reserves a whole source. Seven mixed
takes are training regressions; the reserved source measures transfer within
this development collection. No reference timing reaches the runtime.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from eval.native_auto import ROOT, analyze, match, read
from scipy.signal import resample_poly

DATA = ROOT / "eval/data"
MODEL = ROOT / "native/pocket"
GAINS = (0.5, 1.0, 2.0)


def load(name):
    return json.loads((DATA / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label_candidates(candidates, events):
    # A model-independent candidate stream may contain several starts inside
    # one acoustic attack. Label every window that reaches that attack, rather
    # than training the same timbre as both a drum and noise. Precursors whose
    # entire 10ms window precedes it remain negative. Scoring stays one-to-one.
    labels = {}
    for i, row in enumerate(candidates):
        event = min(events, key=lambda e: abs(row[0] - e["time"]))
        if -0.010 <= row[0] - event["time"] <= 0.025:
            labels[i] = event["drum"]
    return labels


def corpus(binary, exclude=()):
    audit = load("pocket-real-takes.json")
    iso = load("pocket-isolated-attacks.json")
    rows = []
    sources = []
    for take in audit["takes"] + iso["takes"]:
        if take.get("name") in exclude:
            continue
        path = ROOT / take["source"]
        assert digest(path) == take["sha256"], f"Changed source: {path}"
        sources.append(dict(source=take["source"], sha256=take["sha256"]))
        y, sr = read(path)
        reserved = [
            r["time"] for r in iso["heldout_attacks"] if r["source"] == take["source"]
        ]
        for gain in GAINS:
            candidates = analyze(
                binary, y * gain, sr, include_rejected=True, candidates=True
            )
            labels = label_candidates(candidates, take["events"])
            for i, row in enumerate(candidates):
                # Exclude the complete reserved isolated attack and its tail,
                # before gain augmentation. Never train on constructed tests.
                if reserved and (
                    row[0] < 0.025
                    or row[0] > len(y) / sr - 0.1
                    or any(abs(row[0] - t) < 0.2 for t in reserved)
                ):
                    continue
                rows.append(
                    dict(
                        take=take.get("name", "isolated"),
                        time=row[0],
                        gain=gain,
                        label=labels.get(i, 3),
                        features=row[4:].tolist(),
                    )
                )
    # Teach attack timbre across changing preceding context using only the
    # non-reserved isolated hits. No test waveform, test ordering or reference
    # groove enters this augmentation.
    rng = np.random.default_rng(93021)
    sr = 48000
    clips = []
    for take in iso["takes"]:
        y, source_sr = read(ROOT / take["source"])
        reserved = [
            r["time"] for r in iso["heldout_attacks"] if r["source"] == take["source"]
        ]
        for event in take["events"]:
            if any(abs(event["time"] - t) < 0.2 for t in reserved):
                continue
            start = round((event["time"] - 0.012) * source_sr)
            clip = y[start : start + round(0.19 * source_sr)].copy()
            n = round(0.01 * source_sr)
            clip[-n:] *= np.linspace(1, 0, n)
            clip[: round(0.002 * source_sr)] *= np.linspace(
                0, 1, round(0.002 * source_sr)
            )
            clips.append(
                (
                    resample_poly(clip, sr, source_sr),
                    event["time"] - start / source_sr,
                    event["drum"],
                )
            )
    for scene in range(8):
        y = np.zeros(sr * 32, dtype="float32")
        refs = []
        t = 0.05
        for index in rng.permutation(np.tile(np.arange(len(clips)), 2)):
            clip, onset, drum = clips[index]
            clip = clip[: round(rng.uniform(0.10, 0.19) * sr)].copy()
            clip[-round(0.008 * sr) :] *= np.linspace(1, 0, round(0.008 * sr))
            t += rng.uniform(0.12, 0.32)
            start = round(t * sr)
            if start + len(clip) > len(y):
                break
            y[start : start + len(clip)] += clip * rng.uniform(0.5, 1.5)
            refs.append(dict(t=t + onset, drum=drum))
        for gain in GAINS:
            candidates = analyze(
                binary, y * gain, sr, include_rejected=True, candidates=True
            )
            labels = label_candidates(
                candidates, [dict(time=r["t"], drum=r["drum"]) for r in refs]
            )
            for i, row in enumerate(candidates):
                rows.append(
                    dict(
                        take="isolated_context",
                        time=row[0],
                        gain=gain,
                        label=labels.get(i, 3),
                        features=row[4:].tolist(),
                    )
                )
    return rows, sources, iso


def export(model, x, namespace="model"):
    def vector(v):
        return "{" + ", ".join(format(float(a), ".17g") for a in v) + "}"

    roots, nodes, leaves = [], [], []
    node_ids, leaf_ids = {}, {}

    def intern(node):
        if node not in node_ids:
            node_ids[node] = len(nodes)
            nodes.append(node)
        return node_ids[node]

    for estimator, weight in zip(model.estimators_, model.tree_weights_):
        tree = estimator.tree_

        def visit(i):
            if tree.children_left[i] < 0:
                votes = tree.value[i][0]
                key = tuple(
                    votes / votes.sum() * weight * getattr(estimator, "vote_mask", 1.0)
                )
                if key not in leaf_ids:
                    leaf_ids[key] = len(leaves)
                    leaves.append(key)
                return intern((-1, 0.0, leaf_ids[key], 0))
            left, right = visit(tree.children_left[i]), visit(tree.children_right[i])
            # Exact shared subtrees and equal child distributions are lossless.
            if left == right:
                return left
            return intern((int(tree.feature[i]), float(tree.threshold[i]), left, right))

        roots.append(visit(0))
    scale = np.maximum(x.std(0), 0.2)
    scale[6:12] /= 2
    text = "// Generated by python -m eval.real_auto train. Do not edit.\n#pragma once\n#include <array>\nnamespace pocket::model {\n"
    text += "inline constexpr double windowSeconds = .010;\n"
    text += (
        f"inline constexpr std::array<double,{x.shape[1]}> scale = "
        + vector(scale)
        + ";\n"
    )
    text += "struct Node { int feature; double threshold; int left, right; };\n"
    text += (
        f"inline constexpr std::array<Node,{len(nodes)}> nodes = {{{{\n"
        + ",\n".join(
            "{" + f"{f}, {t:.17g}, {left}, {right}" + "}" for f, t, left, right in nodes
        )
        + "\n}};\n"
    )
    text += (
        f"inline constexpr std::array<std::array<double,4>,{len(leaves)}> leafVotes = {{{{\n"
        + ",\n".join(vector(v) for v in leaves)
        + "\n}};\n"
    )
    text += (
        f"inline constexpr std::array<int,{len(roots)}> roots = "
        + str(roots).replace("[", "{").replace("]", "}")
        + ";\n}\n"
    )
    return text.replace("namespace pocket::model", f"namespace pocket::{namespace}")


def score(rows, take):
    refs = take["events"]
    pairs = match(rows[:, 0], [dict(t=e["time"]) for e in refs], 0.025)
    used = {i for i, _ in pairs}
    found = {j for _, j in pairs}
    wrong = [
        dict(time=float(rows[i, 0]), expected=refs[j]["drum"], actual=int(rows[i, 1]))
        for i, j in pairs
        if int(rows[i, 1]) != refs[j]["drum"]
    ]
    missed = [e for j, e in enumerate(refs) if j not in found]
    extra = [
        dict(time=float(r[0]), drum=int(r[1]))
        for i, r in enumerate(rows)
        if i not in used
    ]
    return dict(
        take=take["name"],
        reference_hits=len(refs),
        detected=len(rows),
        correct=len(pairs) - len(wrong),
        wrong=wrong,
        missed=missed,
        extra=extra,
    )


def summarize(scores):
    return dict(
        reference_hits=sum(s["reference_hits"] for s in scores),
        correct=sum(s["correct"] for s in scores),
        missed=sum(len(s["missed"]) for s in scores),
        wrong=sum(len(s["wrong"]) for s in scores),
        extra=sum(len(s["extra"]) for s in scores),
    )


ENSEMBLE = [
    dict(trees=200, criterion="entropy", isolated_weight=1.0, vote_weight=0.9),
    dict(trees=100, criterion="gini", isolated_weight=3.0, vote_weight=0.1),
]
PARAMETERS = dict(max_depth=12, min_samples_leaf=1, random_state=42)


def fit_model(x, y, takes):
    from sklearn.ensemble import ExtraTreesClassifier

    models = []
    weights = []
    for expert in ENSEMBLE:
        model = ExtraTreesClassifier(
            n_estimators=expert["trees"], criterion=expert["criterion"], **PARAMETERS
        )
        model.fit(
            x,
            y,
            sample_weight=np.where(
                np.char.startswith(takes, "isolated"), expert["isolated_weight"], 1.0
            ),
        )
        models.append(model)
        weights.extend([expert["vote_weight"] / expert["trees"]] * expert["trees"])
    result = models[0]
    result.estimators_ = [tree for model in models for tree in model.estimators_]
    result.tree_weights_ = weights
    return result


def predict_model(model, x):
    votes = np.zeros((len(x), 4))
    for tree, weight in zip(model.estimators_, model.tree_weights_):
        votes += tree.predict_proba(x) * weight
    return votes.argmax(1)


def report(binary):
    audit = load("pocket-real-takes.json")
    baseline = load("pocket02-real-predictions.json")
    before, after = [], []
    for take in audit["takes"]:
        y, sr = read(ROOT / take["source"])
        rows = analyze(binary, y, sr)
        assert np.all(rows[:, 3] - rows[:, 0] <= 0.010001), "latency increased"
        before.append(score(np.array(baseline["predictions"][take["name"]]), take))
        after.append(score(rows, take))
    return dict(
        reference=audit["method"],
        tolerance_ms=25,
        scope="Full untrimmed recordings: seven training sources and the reserved uptempo source. Development regression, not untouched-test accuracy.",
        before=dict(commit=baseline["commit"], summary=summarize(before), takes=before),
        after=dict(summary=summarize(after), takes=after),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("train", "report"))
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--cache", type=Path)
    a = p.parse_args()
    if a.command == "train":
        from eval.transfer_auto import train

        train(a.binary.resolve(), a.cache)
    else:
        result = json.dumps(report(a.binary.resolve()), indent=2) + "\n"
        if a.output:
            a.output.write_text(result)
        print(result)


if __name__ == "__main__":
    main()
