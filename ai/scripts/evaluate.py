"""Evaluate a trained model and apply the "is the merge enough?" decision rule.

Evaluates on the public test split and, when it exists, on the Egyptian test set
(data/egypt_test/data.yaml). The decision is taken on the EGYPTIAN set only; the public
number is a sanity check.

Decision rule (agreed before testing, see proposal §AI plan):
  speed_bump recall >= 0.75 and precision >= 0.70  -> merge is enough
  0.50 <= recall < 0.75                            -> targeted top-up of the failure cases
  recall < 0.50                                    -> build a proper Egyptian training set

Usage:  python scripts/evaluate.py --weights runs/<run>/weights/best.pt
"""
import argparse, json
from pathlib import Path

import torch
from ultralytics import YOLO

import espdet_support

espdet_support.enable()   # lets ESPDet-Pico weights load; no effect otherwise

ROOT = Path(__file__).resolve().parents[1]
ENOUGH_RECALL, ENOUGH_PRECISION, TOPUP_RECALL = 0.75, 0.70, 0.50


def per_class(metrics, names):
    out = {}
    present = list(metrics.box.ap_class_index)  # class_result() indexes by position in this list
    for i, n in names.items():
        p, r, map50, map5095 = metrics.box.class_result(present.index(i)) if i in present else (0, 0, 0, 0)
        out[n] = {'precision': round(float(p), 3), 'recall': round(float(r), 3),
                  'mAP50': round(float(map50), 3), 'mAP50-95': round(float(map5095), 3)}
    return out


def verdict(cls):
    sb = cls.get('speed_bump', {})
    r, p = sb.get('recall', 0), sb.get('precision', 0)
    if r >= ENOUGH_RECALL and p >= ENOUGH_PRECISION:
        return 'ENOUGH: public merge generalises to Egyptian speed bumps'
    if r >= TOPUP_RECALL:
        return 'TOP-UP: collect 50-100 images of the failure cases only'
    return 'NOT ENOUGH: build a proper Egyptian training set'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--device', default='mps' if torch.backends.mps.is_available() else 'cpu')
    ap.add_argument('--public-data', default=ROOT / 'data/merged/data.yaml', type=Path)
    ap.add_argument('--egypt-data', default=ROOT / 'data/egypt_test/data.yaml', type=Path)
    ap.add_argument('--imgsz', type=int, default=640, help='use the same size the model was trained at')
    a = ap.parse_args()
    model = YOLO(a.weights)
    results = {}
    sets = {'public_test': a.public_data, 'egypt_test': a.egypt_data}
    for tag, yml in sets.items():
        if not yml.exists():
            results[tag] = 'missing'
            continue
        run = Path(a.weights).parents[1].name
        m = model.val(data=str(yml), split='test', device=a.device, plots=True, imgsz=a.imgsz,
                      project=str(ROOT / 'runs'), name=f'eval_{tag}_{run}', exist_ok=True)
        results[tag] = {'overall_mAP50': round(float(m.box.map50), 3), 'per_class': per_class(m, model.names)}
    if isinstance(results.get('egypt_test'), dict):
        results['decision'] = verdict(results['egypt_test']['per_class'])
    else:
        results['decision'] = 'PENDING: no Egyptian test set yet (see README, step 3)'
    out = Path(a.weights).parents[1] / 'evaluation.json'
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
