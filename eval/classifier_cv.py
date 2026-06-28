"""Cross-validate the production drum classifier — the honest accuracy number.

``eval/run_eval.py`` scores the *pipeline* on the tiny corpus; this scores the
*classifier features* (``devices.drum.features.drum_features``) on the training
data with leave-one-out + held-out-take CV and a confusion matrix, so a feature
or data change can be measured WITHOUT Live and without overfit-on-train
flattery.

Run: ``uv run python -m eval.classifier_cv``
"""

from __future__ import annotations

from eval.featurelab import cross_val, labeled_onsets
from mouthflow.devices.drum.features import drum_features


def main() -> int:
    rows = labeled_onsets()
    cross_val(drum_features, "production (drum_features)", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
