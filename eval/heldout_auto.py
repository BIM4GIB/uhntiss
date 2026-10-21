"""Whole-source held-out Auto replay at 48 kHz, with native model identity checks.

All derivatives share their parent's take id. A fold's test source is excluded
before fitting or computing model scales. This remains a development estimate:
these recordings and their failures have already informed engineering choices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from eval.native_auto import ROOT, analyze, read
from eval.real_auto import corpus, load, score, summarize
from eval.transfer_auto import (
    fit,
    predict as predict_model,
    rate_examples,
    recovery_corpus,
)

RATE = 48000
GAINS = (1.0, 0.5, 0.25)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_fold(folder, header):
    """Use an isolated source tree; quoted includes cannot find a shipping model."""
    include = folder / "include/pocket"
    source = folder / "src"
    include.mkdir(parents=True)
    source.mkdir()
    model_hash = hashlib.sha256(header.encode()).hexdigest()
    (include / "attack_model.hpp").write_text(
        header
        + f'\nnamespace pocket::model {{ inline constexpr char auditSignature[] = "{model_hash}"; }}\n'
    )
    (include / "engine.hpp").write_bytes(
        (ROOT / "native/pocket/include/pocket/engine.hpp").read_bytes()
    )
    (source / "engine.cpp").write_text(
        (ROOT / "native/pocket/src/engine.cpp").read_text()
        + "\nnamespace pocket { const char* compiledAuditSignature() noexcept { return model::auditSignature; } }\n"
    )
    obj = folder / "engine.o"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-I",
            str(include.parent),
            "-c",
            str(source / "engine.cpp"),
            "-o",
            str(obj),
        ],
        check=True,
        capture_output=True,
    )
    for name in ("analyze", "classify", "stream"):
        text = (ROOT / f"native/pocket/tools/{name}.cpp").read_text()
        if name == "classify":
            text = text.replace(
                "int main(int argc, char **argv) {",
                "namespace pocket { const char* compiledAuditSignature() noexcept; }\n"
                "int main(int argc, char **argv) {\n"
                '  if (argc > 1 && std::string(argv[1]) == "--signature") { std::cout << pocket::compiledAuditSignature(); return 0; }',
            )
        tool = source / f"{name}.cpp"
        tool.write_text(text)
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                "-O2",
                "-I",
                str(include.parent),
                str(tool),
                str(obj),
                "-o",
                str(folder / name),
            ],
            check=True,
            capture_output=True,
        )
    actual = subprocess.check_output(
        [str(folder / "classify"), "--signature"], text=True
    )
    assert actual == model_hash, "native fold compiled a different model"
    return model_hash


def check_parity(binary, model, x, sample_rate=48000):
    rng = np.random.default_rng(36021)
    a, b = rng.integers(0, len(x), (2, 256))
    mix = rng.uniform(0.1, 0.9, (256, 1))
    probes = np.concatenate((x, x[a] * mix + x[b] * (1 - mix)))
    payload = "\n".join(" ".join(format(v, ".17g") for v in row) for row in probes)
    actual = np.fromstring(
        subprocess.run(
            [str(binary), str(sample_rate)],
            input=payload,
            text=True,
            capture_output=True,
            check=True,
        ).stdout,
        sep=" ",
    )
    assert np.array_equal(actual, predict_model(model, probes, sample_rate)), (
        "native/Python model disagreement"
    )
    return len(probes)


def check_capture(folder, audio, rows):
    checks = []
    for start in np.arange(0, len(audio) / RATE, 4.8):
        raw = folder / "capture.f32"
        p = subprocess.run(
            [str(folder / "stream"), str(RATE), "100", str(raw), str(start / 0.6)],
            input=audio.astype("<f4").tobytes(),
            capture_output=True,
            check=True,
        )
        events = np.array(
            [
                list(map(float, line.split(",")))
                for line in p.stdout.decode().splitlines()
            ]
        ).reshape(-1, 3)
        expected = rows[(rows[:, 0] >= start) & (rows[:, 0] < start + 4.8)]
        assert len(events) == len(expected), "capture lost or duplicated a detection"
        error = 0.0
        if len(events):
            assert np.array_equal(events[:, 1], expected[:, 1]), (
                "capture changed a voice"
            )
            assert np.max(abs(events[:, 2] - expected[:, 2])) < 1e-6, (
                "capture changed dynamics"
            )
            error = float(
                np.max(abs(events[:, 0] * 0.6 + start - expected[:, 0])) * RATE
            )
            assert error < 1.01, "capture moved the detected onset"
        checks.append(
            dict(start_seconds=float(start), hits=len(events), max_error_samples=error)
        )
    return checks


def evaluate(binary, cache=None):
    rows, sources, _ = corpus(binary)
    if cache:
        cache.write_text(json.dumps(rows))
    recovery_rows = recovery_corpus(binary)
    rate_rows = rate_examples(binary)
    groups = np.array([r["take"] for r in rows])
    audit = load("pocket-real-takes.json")
    folds = []
    with tempfile.TemporaryDirectory(prefix="pocket-heldout-") as tmp:
        for take in audit["takes"]:
            keep = groups != take["name"]
            train_groups = sorted(set(groups[keep]))
            assert take["name"] not in train_groups
            model, header, probes = fit(
                rows, recovery_rows, rate_rows, exclude=(take["name"],)
            )
            folder = Path(tmp) / take["name"]
            model_hash = compile_fold(folder, header)
            parity = check_parity(folder / "classify", model, probes)
            highrate_parity = check_parity(folder / "classify", model, probes, 96000)
            audio, sr = read(ROOT / take["source"])
            assert digest(ROOT / take["source"]) == take["sha256"], (
                "test source changed"
            )
            audio = resample_poly(audio, RATE, sr)
            cases = []
            for gain in GAINS:
                samples = audio * gain
                detected = analyze(folder / "analyze", samples, RATE)
                assert np.all(detected[:, 3] - detected[:, 0] <= 0.010001), (
                    "decision window increased"
                )
                captured = check_capture(folder, samples, detected)
                cases.append(
                    dict(gain=gain, score=score(detected, take), capture=captured)
                )
            cut = len(audio) // 2
            changed = audio.copy()
            changed[cut:] = np.random.default_rng(4).normal(0, 0.1, len(audio) - cut)
            original = analyze(folder / "analyze", audio, RATE, include_rejected=True)
            altered = analyze(folder / "analyze", changed, RATE, include_rejected=True)
            assert np.array_equal(
                original[original[:, 3] < cut / RATE],
                altered[altered[:, 3] < cut / RATE],
            ), "future audio changed a completed decision"
            fold = dict(
                test_take=take["name"],
                test_source=take["source"],
                test_sha256=take["sha256"],
                training_groups=train_groups,
                training_sources=[s for s in sources if s["source"] != take["source"]],
                training_rows=int(keep.sum()),
                native_model_sha256=model_hash,
                parity_rows=parity,
                highrate_parity_rows=highrate_parity,
                causality="pass",
                cases=cases,
            )
            folds.append(fold)
            print(
                take["name"],
                [(c["gain"], c["score"]["correct"]) for c in cases],
                flush=True,
            )
    result = dict(
        scope="Whole-source held-out development replay; not an untouched or unseen-performer benchmark.",
        sample_rate=RATE,
        audit_sha256=digest(ROOT / "eval/data/pocket-real-takes.json"),
        engine_sha256=digest(ROOT / "native/pocket/src/engine.cpp"),
        summaries=[
            dict(gain=gain, **summarize([f["cases"][i]["score"] for f in folds]))
            for i, gain in enumerate(GAINS)
        ],
        folds=folds,
    )

    info = json.loads((ROOT / "native/pocket/auto-model.json").read_text())
    deployment_fold = next(f for f in folds if f["test_take"] == "live_6_uptempo")
    deployed_hash = digest(ROOT / "native/pocket/include/pocket/attack_model.hpp")
    assert deployed_hash == info["compiled_model_sha256"], (
        "deployment header/provenance mismatch"
    )
    assert "live_6_uptempo" not in info["training_groups"], (
        "deployment trained on the reserved take"
    )
    # libm/std reductions can differ across platforms. The fold's embedded
    # fingerprint and Python/native parity are always exact on its own host.
    result["deployment_model_matches_reserved_fold"] = (
        deployed_hash == deployment_fold["native_model_sha256"]
    )
    baseline = json.loads(
        (ROOT / "native/pocket/auto-heldout-pocket03.json").read_text()
    )
    for before, after in zip(baseline["summaries"], result["summaries"]):
        assert before["gain"] == after["gain"]
        assert after["correct"] >= before["correct"], (
            "held-out correct voices regressed"
        )
        for metric in ("missed", "wrong", "extra"):
            assert after[metric] <= before[metric], f"held-out {metric} regressed"
    result["baseline_sha256"] = digest(
        ROOT / "native/pocket/auto-heldout-pocket03.json"
    )
    result["release_gate"] = (
        "pass: no aggregate correct/missed/wrong/extra regression at any tested gain"
    )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--cache", type=Path)
    a = p.parse_args()
    result = evaluate(a.binary.resolve(), a.cache)
    a.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summaries"], indent=2))


if __name__ == "__main__":
    main()
