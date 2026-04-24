"""Baseline: Ableton's native 'Convert Drums to New MIDI Track' for A/B.

Ableton's own audio-to-MIDI is the natural baseline. There's no socket
endpoint for it — the workflow is manual, once per clip:

    1. Drag the .wav onto an audio track.
    2. Right-click the clip, Convert -> Convert Drums to New MIDI Track.
    3. On the new MIDI track, right-click the converted clip, Export MIDI.
    4. Save as ``<clip_stem>.baseline.mid`` next to the source .wav.

This script manages the plumbing around that: it walks the corpus, lists
which clips have a paired ``.baseline.mid`` and which don't, and prints
the above checklist for the missing ones. ``taste_review.py`` then reads
the prepared pairs.

Run: ``uv run python -m eval.baseline_ableton`` from the repo root.
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = REPO_ROOT / "tests" / "fixtures" / "clips"


def baseline_path_for(wav: Path) -> Path:
    return wav.with_name(f"{wav.stem}.baseline.mid")


def status(corpus: Path) -> dict[str, list[Path]]:
    wavs = sorted(corpus.glob("*.wav"))
    ready = [w for w in wavs if baseline_path_for(w).exists()]
    missing = [w for w in wavs if not baseline_path_for(w).exists()]
    return {"ready": ready, "missing": missing}


def _print_workflow(missing: list[Path]) -> None:
    if not missing:
        return
    print("\nMissing baselines. For each clip below, in Ableton Live:")
    print("  1. Drag the .wav onto an audio track.")
    print("  2. Right-click the clip -> Convert -> Convert Drums to New MIDI Track.")
    print("  3. Right-click the new MIDI clip -> Export MIDI.")
    print("  4. Save as shown:\n")
    for wav in missing:
        print(f"    {wav.name:40s} -> {baseline_path_for(wav).name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args()

    if not args.corpus.exists():
        print(f"Corpus directory not found: {args.corpus}")
        return 1

    info = status(args.corpus)
    total = len(info["ready"]) + len(info["missing"])
    print(f"Baseline status: {len(info['ready'])}/{total} clips ready.")
    for wav in info["ready"]:
        print(f"  [OK]   {wav.stem}")
    for wav in info["missing"]:
        print(f"  [TODO] {wav.stem}")
    _print_workflow(info["missing"])
    return 0 if not info["missing"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
