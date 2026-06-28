"""Cross-validate the drum classifier — the honest accuracy number.

``eval/run_eval.py`` scores the *pipeline* on the tiny corpus; this scores the
*classifier* on the training data with leave-one-out + held-out-take CV and a
confusion matrix, so a feature/model change can be measured WITHOUT Live and
without overfit-on-train flattery.

Reads the same sources as ``train_classifier`` (calibration one-shots + mimic
labeled takes), so after you re-score takes with a new feature set this picks
up the change automatically.

Run: ``uv run python -m eval.classifier_cv``
"""

from __future__ import annotations

from collections import Counter

from eval.train_classifier import (
    FEATURES,
    K,
    _knn,
    _scaler,
    _z,
    load_incontext,
    load_isolated,
)


def _confusion(rows: list[tuple[str, str]], labels: list[str]) -> None:
    """rows = (true, pred). Print a true->pred matrix + per-class recall."""
    conf = {t: Counter() for t in labels}
    for t, p in rows:
        conf[t][p] += 1
    width = max(len(x) for x in labels)
    header = " " * (width + 8) + " ".join(f"{p:>6}" for p in labels)
    print(header)
    for t in labels:
        tot = sum(conf[t].values())
        acc = conf[t][t] / tot if tot else 0.0
        cells = " ".join(f"{conf[t][p]:>6}" for p in labels)
        print(f"  {t:<{width}} n={tot:<3} acc={acc:.2f}  {cells}")


def main() -> int:
    iso = load_isolated()
    ctx = load_incontext()
    allr = iso + ctx
    labels = sorted({r[1] for r in allr})

    print(f"features ({len(FEATURES)}): {FEATURES}")
    print(f"isolated:   {dict(Counter(r[1] for r in iso))}  ({len(iso)})")
    print(f"in-context: {dict(Counter(r[1] for r in ctx))}  ({len(ctx)})\n")

    # Held-out-take: train on isolated + every OTHER take, test on this take —
    # the realistic "does it generalise to a new performance?" number.
    print("held-out take (k-NN accuracy on the unseen take):")
    for take in sorted({r[2] for r in ctx}):
        train = iso + [r for r in ctx if r[2] != take]
        test = [r for r in ctx if r[2] == take]
        mean, std = _scaler([r[0] for r in train])
        Ztr = [_z(r[0], mean, std) for r in train]
        ytr = [r[1] for r in train]
        ok = sum(_knn(Ztr, ytr, _z(x, mean, std), K) == y for x, y, _ in test)
        print(f"  {take:8} {ok}/{len(test)} = {ok / len(test):.2f}")

    # Combined leave-one-out + confusion.
    mean, std = _scaler([r[0] for r in allr])
    Z = [_z(r[0], mean, std) for r in allr]
    y = [r[1] for r in allr]
    rows = []
    for i in range(len(Z)):
        Ztr = [Z[k] for k in range(len(Z)) if k != i]
        ytr = [y[k] for k in range(len(y)) if k != i]
        rows.append((y[i], _knn(Ztr, ytr, Z[i], K)))
    acc = sum(t == p for t, p in rows) / len(rows)
    print(f"\ncombined leave-one-out (k={K}): {acc:.3f}  (n={len(rows)})\n")
    _confusion(rows, labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
