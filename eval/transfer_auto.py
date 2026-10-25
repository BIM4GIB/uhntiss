"""Pocket 05 research models, with source-owned augmentation and quiet transients.

The deployed model reserves the entire uptempo take. Native runtime uses only
exported fixed trees; no Python, test timeline or waveform is shipped as a model.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scipy.signal import resample_poly
from sklearn.ensemble import ExtraTreesClassifier
from eval.native_auto import ROOT, read, analyze
from eval.real_auto import (
    corpus,
    digest,
    export,
    fit_model,
    label_candidates,
    load,
    predict_model,
)

RESERVED = ("live_6_uptempo",)
VOICE_FEATURES = np.r_[0:14, 15:16]


def recovery_corpus(binary, exclude=()):
    rows = []
    iso = load("pocket-isolated-attacks.json")
    for take in load("pocket-real-takes.json")["takes"] + iso["takes"]:
        if take.get("name") in exclude:
            continue
        assert digest(ROOT / take["source"]) == take["sha256"], (
            "training source changed"
        )
        y, sr = read(ROOT / take["source"])
        reserved = [
            e["time"] for e in iso["heldout_attacks"] if e["source"] == take["source"]
        ]
        for rate, gains in [(48000, (0.125, 0.25, 0.5, 1.0, 2.0)), (44100, (1.0,))]:
            audio = resample_poly(y, rate, sr)
            for gain in gains:
                a = analyze(
                    binary,
                    audio * gain,
                    rate,
                    0.003,
                    include_rejected=True,
                    candidates=True,
                )
                labels = label_candidates(a, take["events"])
                for i, r in enumerate(a):
                    if reserved and (
                        r[0] < 0.025
                        or r[0] > len(y) / sr - 0.1
                        or any(abs(r[0] - t) < 0.2 for t in reserved)
                    ):
                        continue
                    rows.append(
                        dict(
                            take=take.get("name", "isolated"),
                            rate=rate,
                            gain=gain,
                            time=r[0],
                            label=labels.get(i, 3),
                            features=r[4:].tolist(),
                        )
                    )
    rng = np.random.default_rng(93021)
    clips = []
    rate = 48000
    for take in iso["takes"]:
        y, sr = read(ROOT / take["source"])
        reserved = [
            e["time"] for e in iso["heldout_attacks"] if e["source"] == take["source"]
        ]
        for e in take["events"]:
            if any(abs(e["time"] - t) < 0.2 for t in reserved):
                continue
            start = round((e["time"] - 0.012) * sr)
            v = y[start : start + round(0.19 * sr)].copy()
            v[-round(0.01 * sr) :] *= np.linspace(1, 0, round(0.01 * sr))
            v[: round(0.002 * sr)] *= np.linspace(0, 1, round(0.002 * sr))
            clips.append(
                (resample_poly(v, rate, sr), e["time"] - start / sr, e["drum"])
            )
    for scene in range(24):
        y = np.zeros(rate * 24, np.float32)
        refs = []
        t = 0.06
        for index in rng.permutation(np.tile(np.arange(len(clips)), 3)):
            v, onset, drum = clips[index]
            start = round(t * rate)
            if start + len(v) > len(y):
                break
            gain = np.exp(rng.uniform(np.log(0.10), np.log(1.8)))
            y[start : start + len(v)] += v * gain
            refs.append(dict(time=t + onset, drum=drum))
            t += rng.uniform(0.060, 0.33)
        a = analyze(binary, y, rate, 0.003, include_rejected=True, candidates=True)
        labels = label_candidates(a, refs)
        for i, r in enumerate(a):
            rows.append(
                dict(
                    take="isolated_context",
                    rate=rate,
                    gain=1,
                    time=r[0],
                    label=labels.get(i, 3),
                    features=r[4:].tolist(),
                )
            )
    return rows


def rate_examples(binary):
    """Only non-reserved isolated calibration attacks, resampled before extraction."""
    iso = load("pocket-isolated-attacks.json")
    rows = []
    for take in iso["takes"]:
        assert digest(ROOT / take["source"]) == take["sha256"]
        audio, source_rate = read(ROOT / take["source"])
        reserved = [
            r["time"] for r in iso["heldout_attacks"] if r["source"] == take["source"]
        ]
        for rate in (48000, 96000):
            for gain in (0.25, 0.5, 1.0, 2.0):
                candidates = analyze(
                    binary,
                    resample_poly(audio, rate, source_rate) * gain,
                    rate,
                    include_rejected=True,
                    candidates=True,
                )
                labels = label_candidates(candidates, take["events"])
                for i, row in enumerate(candidates):
                    if (
                        row[0] < 0.025
                        or row[0] > len(audio) / source_rate - 0.1
                        or any(abs(row[0] - t) < 0.2 for t in reserved)
                    ):
                        continue
                    rows.append(
                        dict(
                            take="isolated",
                            rate=rate,
                            gain=gain,
                            time=row[0],
                            label=labels.get(i, 3),
                            features=row[4:].tolist(),
                        )
                    )
    return rows


def fit_recovery(x, y):
    voice = ExtraTreesClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=2,
        random_state=42,
        max_features=0.8,
        criterion="entropy",
        class_weight="balanced",
    )
    voice.fit(x[y < 3][:, VOICE_FEATURES], y[y < 3])
    reject = ExtraTreesClassifier(
        n_estimators=100,
        max_depth=12,
        min_samples_leaf=2,
        random_state=43,
        max_features="sqrt",
        criterion="entropy",
        class_weight="balanced",
    )
    reject.fit(x, y == 3)
    trees, weights = [], []
    for forest, noise in ((voice, False), (reject, True)):
        for estimator in forest.estimators_:
            tree = estimator.tree_
            features = tree.feature.copy()
            if not noise:
                features[features >= 0] = VOICE_FEATURES[features[features >= 0]]
            values = np.zeros((len(features), 1, 4))
            if noise:
                values[:, 0, 3] = tree.value[:, 0, 1]
                values[:, 0, 0] = tree.value[:, 0, 0]
            else:
                values[:, 0, :3] = tree.value[:, 0, :3]
            trees.append(
                SimpleNamespace(
                    tree_=SimpleNamespace(
                        feature=features,
                        threshold=tree.threshold,
                        children_left=tree.children_left,
                        children_right=tree.children_right,
                        value=values,
                    ),
                    vote_mask=np.array([0, 0, 0, 1]) if noise else 1.0,
                )
            )
            weights.append(1 / len(forest.estimators_))
    return SimpleNamespace(
        voice=voice, reject=reject, estimators_=trees, tree_weights_=weights
    )


def arrays(rows, exclude=()):
    # Group exclusion precedes fitting, normalization and class weights.
    kept = [row for row in rows if row["take"] not in exclude]
    return (
        np.array([r["features"] for r in kept]),
        np.array([r["label"] for r in kept]),
        np.array([r["take"] for r in kept]),
    )


def fit(primary_rows, recovery_rows, rate_rows, exclude=()):
    x, y, groups = arrays(primary_rows, exclude)
    rx, ry, rgroups = arrays(recovery_rows, exclude)
    assert not set(exclude) & (set(groups) | set(rgroups))
    primary = fit_model(x, y, groups)
    hx, hy, hgroups = arrays(primary_rows + rate_rows, exclude)
    highrate = fit_model(hx, hy, hgroups)
    recovery = fit_recovery(rx, ry)
    header = export(primary, x) + "\n" + export(recovery, rx, namespace="recovery")
    header += "\n" + export(highrate, hx, namespace="highrate")
    return (primary, recovery, highrate), header, np.concatenate((x, rx, hx))


def predict(models, x, sample_rate=48000):
    primary, recovery, highrate = models
    if sample_rate > 48000:
        primary = highrate
    result = predict_model(primary, x)
    votes = recovery.voice.predict_proba(x[:, VOICE_FEATURES])
    noise = recovery.reject.predict_proba(x)[:, 1]
    recover = (result == 3) & (noise <= 0.3) & (votes.max(1) >= 0.6)
    result[recover] = votes.argmax(1)[recover]
    return result


def train(binary, cache):
    from eval import transient_auto

    # Skip the reserved source before opening audio or making derivatives.
    rows, sources, iso = corpus(binary, exclude=RESERVED)
    recovery_rows = recovery_corpus(binary, exclude=RESERVED)
    rate_rows = rate_examples(binary)
    models, header, probes = fit(rows, recovery_rows, rate_rows, RESERVED)
    transient_rows = transient_auto.corpus(binary)
    _, transient_header, _ = transient_auto.fit(transient_rows)
    if cache:
        cache.write_text(
            json.dumps(
                dict(
                    primary=rows,
                    recovery=recovery_rows,
                    rate=rate_rows,
                    transient=transient_rows,
                )
            )
        )
    target = ROOT / "native/pocket"
    (target / "include/pocket/attack_model.hpp").write_text(header)
    (target / "include/pocket/transient_model.hpp").write_text(transient_header)
    import sklearn

    info = dict(
        sklearn_version=sklearn.__version__,
        version=5,
        feature_version=3,
        enrollment=dict(state_version=5, max_voices=16, max_examples=30, commit_features=120,
                        commit_slices_ms=[0, 10, 40, 80], next_onset_truncation=True, negative_bank=30),
        features=17,
        window_ms=10,
        history_ms=30,
        compiled_model_sha256=hashlib.sha256(header.encode()).hexdigest(),
        transient_model_sha256=hashlib.sha256(transient_header.encode()).hexdigest(),
        combined_model_sha256=hashlib.sha256(
            (header + "\n" + transient_header).encode()
        ).hexdigest(),
        audit_sha256=digest(ROOT / "eval/data/pocket-real-takes.json"),
        isolated_audit_sha256=digest(ROOT / "eval/data/pocket-isolated-attacks.json"),
        deployment_reserved_takes=list(RESERVED),
        training=sources,
        training_groups=sorted({r["take"] for r in rows + recovery_rows}),
        primary_training_rows=len(rows),
        highrate_training_rows=len(rows) + len(rate_rows),
        highrate_policy="Above 48 kHz: same primary forest specification plus isolated calibration at 48/96 kHz and .25/.5/1/2 gains. Replaces the primary vote; never runs both primary forests.",
        recovery_training_rows=len(recovery_rows),
        transient=dict(
            training_rows=len(transient_rows),
            training_groups=sorted({r["take"] for r in transient_rows}),
            sample_rate=48000,
            features=23,
            window_ms=10,
            band_history_ms=12,
            fast_attack_ms=0.4,
            fast_release_ms=2,
            slow_ms=8,
            novelty_min=1.65,
            reset_novelty_max=1.1,
            reset_ms=1,
            gate_ratio=0.3,
            raw_velocity_max=0.22,
            kick_snare_min=0.7,
            hat_min=0.8,
            forest="200 ExtraTrees, depth14, leaf2, entropy, max_features=.8, balanced, seed5042",
            augmentation="24 isolated overlap scenes, 25–180ms gaps, .125–1.5 gain, seed5042. Reserved isolated attacks and whole uptempo take excluded.",
            scope="Quiet recovery at exactly 48 kHz; existing path at other rates. Permanent reserved source excluded from this model in every fold.",
        ),
        heldout_attacks=iso["heldout_attacks"],
        exclusions="Whole reserved source excluded before reading or augmentation. Every reserved isolated attack and its +/-200ms neighbourhood excluded at every gain. No test waveform or ordering enters augmentation.",
        primary="Pocket03 ensemble: 200 entropy + 100 gini trees, depth12, seed42",
        recovery="200 voice trees (seed42) + 100 rejection trees (seed43), depth12, leaf2, balanced classes",
        policy=dict(
            rejected_noise_max=0.3,
            rejected_voice_min=0.6,
            quiet_gate_ratio=0.3,
            quiet_hat_noise_max=0.4,
            quiet_raw_velocity_max=0.22,
            refractory_ms=35,
        ),
        recovery_augmentation=dict(
            rates=[48000, 44100],
            gains_48k=[0.125, 0.25, 0.5, 1, 2],
            gains_44100=[1],
            isolated_context_scenes=24,
            context_seed=93021,
        ),
        scope="Source-held-out development model; recordings informed engineering choices. Not an untouched test or unseen-performer claim.",
    )
    (target / "auto-model.json").write_text(json.dumps(info, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: info[k]
                for k in (
                    "deployment_reserved_takes",
                    "primary_training_rows",
                    "recovery_training_rows",
                    "compiled_model_sha256",
                )
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    train(args.binary.resolve(), args.cache)


if __name__ == "__main__":
    main()
