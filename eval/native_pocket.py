"""Report the *causal native* detector on existing fixtures; never loads Live.

This is research tooling, not a device dependency. Fixture MIDI is an
approximate, auto-labelled reference, not a human annotation of vocal onsets.
The old Python classifier's scores do not describe this new detector.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from eval.run_eval import _load_ground_truth, _match_onsets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path('native/pocket/build/pocket_analyze'))
    parser.add_argument('--gate', type=float, default=.018)
    args = parser.parse_args()
    results = []
    for wav in sorted(Path('tests/fixtures/clips').glob('*.wav')):
        samples, sr = sf.read(wav, dtype='float32')
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        proc = subprocess.run([str(args.binary), str(sr), str(args.gate)],
                              input=samples.astype('<f4').tobytes(), capture_output=True, check=True)
        rows = [list(map(float, row.split(','))) for row in proc.stdout.decode().splitlines()]
        pred = [(row[0], (36, 38, 42)[int(row[1])], round(row[2]*127)) for row in rows]
        gt = _load_ground_truth(wav.with_suffix('.mid'))
        tp, fp, fn, matched = _match_onsets(pred, gt)
        results.append(dict(clip=wav.name, hits=len(rows), tp=tp, fp=fp, fn=fn,
                            onset_f1=2*tp/max(1, 2*tp+fp+fn),
                            matched_class_accuracy=sum(pred[i][1] == (42 if gt[j][1] == 46 else gt[j][1])
                                                       for i, j in matched)/max(1, tp),
                            decision_after_onset_ms=float(np.mean([(r[3]-r[0])*1000 for r in rows]))))
    print(json.dumps({'mode': 'uncalibrated Auto; report only', 'clips': results}, indent=2))


if __name__ == '__main__':
    main()
