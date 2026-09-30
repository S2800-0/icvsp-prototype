"""Turn the team's corrected labels into the Egyptian test set used by evaluate.py.

Takes a YOLO-format export (a folder or .zip, e.g. CVAT "Ultralytics YOLO Detection 1.0"),
collects every labelled image regardless of how the export split it, maps classes by NAME
onto the ICVSP classes, and writes

  data/egypt_test/test/{images,labels}/
  data/egypt_test/data.yaml

It also reports how many objects came from each area (the part of the file name before the
first "_", e.g. maadi_malak_1_0003.jpg → maadi), because the decision needs bumps from
several different places.

Usage:  python scripts/prepare_egypt_test.py path/to/export(.zip)
"""
import argparse, shutil, tempfile, zipfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMG_EXT = {'.jpg', '.jpeg', '.png'}
TARGET_SPEED_BUMPS = 120


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export', type=Path)
    ap.add_argument('--out', default=ROOT / 'data/egypt_test', type=Path)
    a = ap.parse_args()

    ours = yaml.safe_load((ROOT / 'configs/classes.yaml').read_text())['classes']
    our_id = {name: i for i, name in ours.items()}

    tmp = None
    src = a.export
    if src.suffix == '.zip':
        tmp = Path(tempfile.mkdtemp())
        zipfile.ZipFile(src).extractall(tmp)
        src = tmp
    ymls = [y for y in src.rglob('*.yaml') if 'names' in (yaml.safe_load(y.read_text()) or {})]
    if not ymls:
        raise SystemExit('No data.yaml with class names found in the export.')
    names = yaml.safe_load(ymls[0].read_text())['names']
    names = names if isinstance(names, dict) else dict(enumerate(names))
    remap = {}
    for i, n in names.items():
        key = str(n).strip().lower().replace(' ', '_').replace('-', '_')
        if key not in our_id:
            raise SystemExit(f'Export class "{n}" is not one of {list(our_id)}. Rename it in the labelling tool.')
        remap[int(i)] = our_id[key]

    test = a.out / 'test'
    if test.exists():
        shutil.rmtree(test)
    (test / 'images').mkdir(parents=True)
    (test / 'labels').mkdir(parents=True)

    per_class, per_area, images, unlabelled = Counter(), defaultdict(Counter), 0, 0
    for img in sorted(p for p in src.rglob('*') if p.suffix.lower() in IMG_EXT):
        parts = list(img.parts)
        lab = None
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == 'images':
                lab = Path(*parts[:i], 'labels', *parts[i + 1:]).with_suffix('.txt')
                break
        if lab is None or not lab.exists():
            unlabelled += 1
            continue
        lines = []
        for ln in lab.read_text().splitlines():
            f = ln.split()
            if len(f) >= 5:
                c = remap[int(float(f[0]))]
                lines.append(' '.join([str(c)] + f[1:5]))
                per_class[ours[c]] += 1
                per_area[img.stem.split('_')[0]][ours[c]] += 1
        shutil.copy2(img, test / 'images' / img.name)
        (test / 'labels' / f'{img.stem}.txt').write_text('\n'.join(lines) + ('\n' if lines else ''))
        images += 1

    (a.out / 'data.yaml').write_text(yaml.safe_dump(
        {'path': str(a.out.resolve()), 'train': 'test/images', 'val': 'test/images', 'test': 'test/images',
         'names': ours}, sort_keys=False))
    if tmp:
        shutil.rmtree(tmp)

    print(f'Egyptian test set: {images} images ({unlabelled} skipped without a label file)')
    print('  objects: ' + ', '.join(f'{k} {v}' for k, v in per_class.items()))
    print('  by area: ' + '; '.join(f'{area}: ' + ', '.join(f'{k} {v}' for k, v in c.items())
                                     for area, c in sorted(per_area.items())))
    sb = per_class.get('speed_bump', 0)
    if sb < TARGET_SPEED_BUMPS or len(per_area) < 5:
        print(f'  ⚠ Target is about {TARGET_SPEED_BUMPS} speed bumps from at least 5 areas '
              f'(have {sb} from {len(per_area)}). Results will be less certain.')


if __name__ == '__main__':
    main()
