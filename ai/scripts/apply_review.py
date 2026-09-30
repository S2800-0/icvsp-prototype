"""Turn the Y / N / E decisions from tools/review.html into training data, so only the unclear
frames need CVAT.

  Y  the model's boxes are right   -> <name>.zip             (frames + the model's labels)
  N  no hazard, the boxes are wrong -> background_<name>.zip  (frames only: "this is not a bump")
  E  needs fixing                   -> cvat_<name>.zip        (frames + pre-labels to import into CVAT)

After fixing the E frames in CVAT, export them (Ultralytics YOLO Detection 1.0, with images) and
upload that export next to the other two zips in the private Kaggle dataset icvsp-egypt-train.
Frames without a decision are left out.

Usage:  python scripts/apply_review.py review.csv --name youtube_batch1 [--prelabels data/egypt_test/prelabels]
"""
import argparse, csv, shutil, zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def write_zip(path: Path, frames, pre: Path, with_labels: bool, names):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in frames:
            z.write(pre / 'images/train' / f, f'images/train/{f}')
            if with_labels:
                lab = pre / 'labels/train' / f'{Path(f).stem}.txt'
                z.writestr(f'labels/train/{Path(f).stem}.txt', lab.read_text() if lab.exists() else '')
        if with_labels:
            z.writestr('data.yaml', yaml.safe_dump({'path': '.', 'train': 'images/train', 'names': names}, sort_keys=False))
            z.writestr('train.txt', ''.join(f'images/train/{f}\n' for f in frames))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('review', type=Path)
    ap.add_argument('--name', required=True, help='batch name starting with youtube_ or egypt_, e.g. youtube_batch1')
    ap.add_argument('--prelabels', default=ROOT / 'data/egypt_test/prelabels', type=Path)
    ap.add_argument('--out', default=ROOT / 'data/reviewed', type=Path)
    a = ap.parse_args()
    if not a.name.startswith(('youtube_', 'egypt_')):
        raise SystemExit('--name must start with youtube_ or egypt_ (the merge uses it to split by video)')

    names = yaml.safe_load((a.prelabels / 'data.yaml').read_text())['names']
    groups = {'Y': [], 'N': [], 'E': []}
    for r in csv.DictReader(open(a.review)):
        if (a.prelabels / 'images/train' / r['frame']).exists():
            groups[r['decision'].strip().upper()].append(r['frame'])
        else:
            print(f"not in {a.prelabels}: {r['frame']}")
    a.out.mkdir(parents=True, exist_ok=True)
    tail = a.name.split('_', 1)[1]
    out = {'Y': a.out / f'{a.name}.zip', 'N': a.out / f'background_{tail}.zip', 'E': a.out / f'cvat_{tail}.zip'}
    for k, frames in groups.items():
        if frames:
            write_zip(out[k], frames, a.prelabels, k != 'N', names)
            print(f'{k}: {len(frames):4d} frames -> {out[k]}')
    if groups['E']:
        print(f'\nImport {out["E"].name} into CVAT (Ultralytics YOLO Detection 1.0), fix it, export it as '
              f'{a.name}_fixed.zip. Upload that export, {out["Y"].name} and {out["N"].name} to the private '
              'Kaggle dataset icvsp-egypt-train.')


if __name__ == '__main__':
    main()
