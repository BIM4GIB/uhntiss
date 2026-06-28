"""Train the per-user drum classifier (k-NN) from calibration + mimic takes.

Extraction goes through ``devices.drum.features.drum_features`` (the same 10
features the classifier uses at inference) over labelled onsets re-derived from
source audio + the mimic ground-truth grids (``eval.featurelab.labeled_onsets``)
— so training, inference, and the feature lab can never disagree, and new
recorded takes are picked up automatically.

k-NN (not nearest-centroid) because a drum class is multi-modal (a hat is bright
slow, darker fast); k-NN keeps the exemplars and votes by nearest neighbours.

Run: ``uv run python -m eval.train_classifier``
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from eval.featurelab import cross_val, labeled_onsets
from mouthflow.devices.drum.features import FEATURES, drum_features

REPO = Path(__file__).resolve().parent.parent
MODEL_OUT = REPO / "mouthflow" / "drum_model.json"

RMS_FLOOR = 0.005
K = 5
LABEL_TO_PITCH = {"kick": 36, "snare": 38, "hat": 42, "clap": 39}


def main() -> int:
    rows = labeled_onsets()
    X = np.array([drum_features(r["audio"], r["sr"], r["t"]) for r in rows], dtype=float)
    y = [r["label"] for r in rows]
    print(f"{len(rows)} labelled onsets  {dict(Counter(y))}\n")

    # Honest generalisation report (held-out-take + LOO + confusion).
    cross_val(drum_features, "drum_features (this model)", rows)

    mean = X.mean(0)
    std = X.std(0)
    std[std == 0] = 1.0
    Z = ((X - mean) / std).tolist()

    MODEL_OUT.write_text(
        json.dumps(
            {
                "type": "knn",
                "k": K,
                "features": FEATURES,
                "mean": mean.tolist(),
                "std": std.tolist(),
                "classes": {lab: LABEL_TO_PITCH.get(lab, 39) for lab in sorted(set(y))},
                "exemplars": Z,
                "labels": y,
                "rms_floor": RMS_FLOOR,
            }
        )
    )
    print(f"\nwrote model -> {MODEL_OUT.relative_to(REPO)} ({len(Z)} exemplars, {len(FEATURES)} features)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
