"""Train the cross-performer drum MLP (``mouthflow/drum_global.json``) on AVP.

The global model is what a fresh install classifies with before the user has
enrolled (tier 2 in ``devices/drum/classify.py``). It is a one-hidden-layer
MLP over the ``all123`` feature set, trained on every annotated onset of the
AVP *Personal* modality (25 performers, repetition + improvisation files).

Its honest generalisation number is NOT its training fit: run
``uv run python -m eval.avp_eval --feats all123 --clf mlp`` for the
leave-one-performer-out figure (68.8 % at the time of writing; the reference
MIT project's unseen-voice number is 60.3 %). This script prints the training
fit only so a broken extraction is caught.

Dataset: Delgado et al., "A New Dataset for Amateur Vocal Percussion
Analysis" (2019), https://zenodo.org/records/3250230, CC BY 4.0 — the
attribution travels inside the model file.

Run: ``uv run python -m eval.train_global``
"""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from pathlib import Path

import numpy as np

from eval import avp
from eval.avp_eval import MLP, featurize
from mouthflow.devices.drum.features import FEATURE_SETS

REPO = Path(__file__).resolve().parent.parent
MODEL_OUT = REPO / "mouthflow" / "drum_global.json"
FEATURE_SET = "all123"
RMS_FLOOR = 0.005
PITCHES = {"kick": 36, "snare": 38, "hat": 42}


def main() -> int:
    if not avp.available():
        print(f"AVP dataset not found at {avp.AVP_DIR}; see eval/avp.py")
        return 2
    rows = avp.load("Personal")
    X = featurize(rows, FEATURE_SET, "gt")
    y = np.array([r["label"] for r in rows])
    print(f"{len(rows)} AVP onsets  {dict(Counter(y.tolist()))}")

    t0 = time.time()
    m = MLP().fit(X, y)
    fit = float((m.predict(X) == y).mean())
    print(f"training fit {fit:.3f} (NOT a generalisation number — see docstring)  {time.time() - t0:.1f}s")

    W1, b1, W2, b2 = (np.round(p, 6).tolist() for p in m.params)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:
        sha = "unknown"
    MODEL_OUT.write_text(
        json.dumps(
            {
                "type": "mlp",
                "feature_set": FEATURE_SET,
                "features": FEATURE_SETS[FEATURE_SET][0],
                "hidden": m.h,
                "classes": list(m.classes),
                "pitches": PITCHES,
                "mean": np.round(m.mean, 6).tolist(),
                "std": np.round(m.std, 6).tolist(),
                "W1": W1,
                "b1": b1,
                "W2": W2,
                "b2": b2,
                "rms_floor": RMS_FLOOR,
                "source": {
                    "dataset": "Amateur Vocal Percussion (AVP) dataset, Personal modality",
                    "citation": "Delgado, McDonald, Xu, Saitis, Sandler (2019). A New Dataset for Amateur Vocal Percussion Analysis. AM'19.",
                    "url": "https://zenodo.org/records/3250230",
                    "license": "CC BY 4.0",
                    "n_events": int(len(rows)),
                    "n_participants": int(len(set(r["participant"] for r in rows))),
                    "trained_at_commit": sha,
                    "training_fit": round(fit, 4),
                    "lopo_accuracy_note": "run eval.avp_eval --feats all123 --clf mlp for the held-out number",
                },
            }
        )
    )
    print(f"wrote {MODEL_OUT} ({MODEL_OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
