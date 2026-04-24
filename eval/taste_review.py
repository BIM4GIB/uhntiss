"""Interactive 1-5 A/B taste rater.

For each clip in the corpus, expects two pre-rendered audio files next
to the source .wav:

    <stem>.mouthflow.wav   # rendered from Mouthflow's MIDI
    <stem>.baseline.wav    # rendered from Ableton's convert-to-MIDI baseline

Plays each A then B, prompts a 1-5 rating on each, writes one CSV row
per clip. Order is randomised per clip so the rater doesn't learn which
track is which. Rerunning appends; pass ``--fresh`` to start over.

Run: ``uv run python -m eval.taste_review``
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = REPO_ROOT / "tests" / "fixtures" / "clips"
DEFAULT_OUT = REPO_ROOT / "eval" / "taste_review.csv"

CSV_FIELDS = [
    "clip_stem",
    "rater",
    "timestamp",
    "mouthflow_rating",
    "baseline_rating",
    "winner",  # mouthflow / baseline / tie
]


def _play(path: Path) -> None:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    sd.play(data, sr)
    sd.wait()


def _prompt_rating(label: str) -> int:
    while True:
        raw = input(f"  {label} rating (1-5): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 5:
            return int(raw)
        print("  enter an integer 1..5")


def _winner(mouthflow: int, baseline: int) -> str:
    if mouthflow > baseline:
        return "mouthflow"
    if baseline > mouthflow:
        return "baseline"
    return "tie"


def _find_pairs(corpus: Path) -> list[tuple[Path, Path, Path]]:
    pairs: list[tuple[Path, Path, Path]] = []
    for wav in sorted(corpus.glob("*.wav")):
        if ".mouthflow" in wav.name or ".baseline" in wav.name:
            continue
        mf = wav.with_name(f"{wav.stem}.mouthflow.wav")
        bl = wav.with_name(f"{wav.stem}.baseline.wav")
        if mf.exists() and bl.exists():
            pairs.append((wav, mf, bl))
    return pairs


def _already_rated(csv_path: Path) -> set[tuple[str, str]]:
    if not csv_path.exists():
        return set()
    done: set[tuple[str, str]] = set()
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            done.add((row["clip_stem"], row["rater"]))
    return done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--rater", default="self")
    parser.add_argument("--fresh", action="store_true", help="Overwrite existing CSV.")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic A/B order.")
    args = parser.parse_args()

    pairs = _find_pairs(args.corpus)
    if not pairs:
        print(
            f"No rendered pairs in {args.corpus}.\n"
            f"Produce <stem>.mouthflow.wav and <stem>.baseline.wav for each clip first."
        )
        return 1

    if args.fresh and args.out.exists():
        args.out.unlink()
    existing = _already_rated(args.out)

    rng = random.Random(args.seed)
    write_header = not args.out.exists()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        for source, mf_path, bl_path in pairs:
            stem = source.stem
            if (stem, args.rater) in existing:
                print(f"skip {stem}: already rated by {args.rater}")
                continue

            print(f"\n--- {stem} ---")
            # Randomise A/B order so the rater can't bias toward "first".
            flipped = rng.random() < 0.5
            a_label, a_path = ("B", bl_path) if flipped else ("A", mf_path)
            b_label, b_path = ("A", mf_path) if flipped else ("B", bl_path)

            print(f"  playing {a_label}..."); _play(a_path)
            print(f"  playing {b_label}..."); _play(b_path)

            # Reveal labels after both are heard so ratings are blind-ish.
            mf_rating = _prompt_rating("mouthflow")
            bl_rating = _prompt_rating("baseline")

            writer.writerow(
                {
                    "clip_stem": stem,
                    "rater": args.rater,
                    "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                    "mouthflow_rating": mf_rating,
                    "baseline_rating": bl_rating,
                    "winner": _winner(mf_rating, bl_rating),
                }
            )
            fh.flush()

    print(f"\nWrote to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
