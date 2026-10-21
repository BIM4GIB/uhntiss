"""48 kHz quiet-transient research. Never imported by the audio device.

The permanent uptempo source is excluded before reading audio. A fold also
excludes its own source before fitting. Synthetic overlap scenes use only
non-reserved isolated calibration attacks, never a test waveform or ordering.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly
from sklearn.ensemble import ExtraTreesClassifier

from eval.native_auto import ROOT, analyze, read
from eval.real_auto import digest, export, label_candidates, load

RATE = 48000
SEED = 5042
RESERVED = ("live_6_uptempo",)


def corpus(binary, exclude=()):
    excluded = set(RESERVED) | set(exclude)
    rows = []
    iso = load("pocket-isolated-attacks.json")

    def candidates(audio, events, name, gain, reserved=()):
        detected = analyze(
            binary, audio, RATE, 0.003, include_rejected=True, transient=True
        )
        labels = label_candidates(detected, events)
        for i, row in enumerate(detected):
            if reserved and (
                row[0] < 0.025
                or row[0] > len(audio) / RATE - 0.1
                or any(abs(row[0] - t) < 0.2 for t in reserved)
            ):
                continue
            rows.append(
                dict(
                    take=name,
                    gain=gain,
                    time=row[0],
                    label=labels.get(i, 3),
                    features=row[4:].tolist(),
                )
            )

    for take in load("pocket-real-takes.json")["takes"] + iso["takes"]:
        name = take.get("name", "isolated")
        if name in excluded:
            continue
        assert digest(ROOT / take["source"]) == take["sha256"]
        audio, sr = read(ROOT / take["source"])
        reserved = [
            e["time"] for e in iso["heldout_attacks"] if e["source"] == take["source"]
        ]
        audio = resample_poly(audio, RATE, sr)
        for gain in (0.125, 0.25, 0.5, 1.0, 2.0):
            candidates(audio * gain, take["events"], name, gain, reserved)

    clips = []
    for take in iso["takes"]:
        if take.get("name", "isolated") in excluded:
            continue
        assert digest(ROOT / take["source"]) == take["sha256"]
        audio, sr = read(ROOT / take["source"])
        reserved = [
            e["time"] for e in iso["heldout_attacks"] if e["source"] == take["source"]
        ]
        for event in take["events"]:
            if any(abs(event["time"] - t) < 0.2 for t in reserved):
                continue
            start = round((event["time"] - 0.012) * sr)
            clip = audio[start : start + round(0.115 * sr)].copy()
            clip[-round(0.008 * sr) :] *= np.linspace(1, 0, round(0.008 * sr))
            clip[: round(0.002 * sr)] *= np.linspace(0, 1, round(0.002 * sr))
            clips.append(
                (
                    resample_poly(clip, RATE, sr),
                    event["time"] - start / sr,
                    event["drum"],
                )
            )
    rng = np.random.default_rng(SEED)
    for _ in range(24):
        audio = np.zeros(RATE * 12, np.float32)
        events = []
        t = 0.06
        for index in rng.permutation(np.tile(np.arange(len(clips)), 4)):
            clip, onset, drum = clips[index]
            start = round(t * RATE)
            if start + len(clip) > len(audio):
                break
            audio[start : start + len(clip)] += clip * np.exp(
                rng.uniform(np.log(0.125), np.log(1.5))
            )
            events.append(dict(time=t + onset, drum=drum))
            t += rng.uniform(0.025, 0.18)
        candidates(audio, events, "isolated_context", 1)
    assert not excluded & {r["take"] for r in rows}
    return rows


def fit(rows, exclude=()):
    excluded = set(RESERVED) | set(exclude)
    kept = [r for r in rows if r["take"] not in excluded]
    x = np.array([r["features"] for r in kept])
    y = np.array([r["label"] for r in kept])
    model = ExtraTreesClassifier(
        n_estimators=200,
        max_depth=14,
        min_samples_leaf=2,
        random_state=SEED,
        max_features=0.8,
        criterion="entropy",
        class_weight="balanced",
    )
    model.fit(x, y)
    model.tree_weights_ = [1 / len(model.estimators_)] * len(model.estimators_)
    return model, export(model, x, namespace="transient"), x


def predict(model, x):
    votes = model.predict_proba(x)
    voice = votes.argmax(1)
    threshold = np.where(voice == 2, 0.8, 0.7)
    voice[votes.max(1) < threshold] = 3
    return voice
