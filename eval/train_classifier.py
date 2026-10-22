"""Train the per-user drum classifier (k-NN) from calibration + mimic takes.

Extraction goes through ``devices.drum.features`` (``FEATURE_SET``, the same
vector the classifier uses at inference; ``all123`` since the AVP study) over labelled onsets re-derived from
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
from mouthflow.devices.drum.features import DEFAULT_FEATURE_SET, FEATURE_SETS

FEATURE_SET = DEFAULT_FEATURE_SET
FEATURES, drum_features = FEATURE_SETS[FEATURE_SET]

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
    Z = np.round((X - mean) / std, 5).tolist()  # z-scored; 5 dp keeps the file small

    MODEL_OUT.write_text(
        json.dumps(
            {
                "type": "knn",
                "k": K,
                "feature_set": FEATURE_SET,
                "features": FEATURES,
                "mean": np.round(mean, 6).tolist(),
                "std": np.round(std, 6).tolist(),
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
