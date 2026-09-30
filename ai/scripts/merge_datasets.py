"""Merge YOLO-format road-hazard datasets into one dataset with the unified ICVSP classes.

For every source folder under data/raw/<source>/ that contains a data.yaml (or a classes.txt),
the script:
  1. remaps source class IDs to the unified scheme in configs/classes.yaml,
  2. drops boxes whose class does not map (e.g. rumble strips, cracks),
  3. removes near-duplicate images across all sources (perceptual dHash),
  4. writes data/merged/{train,val,test}/{images,labels} and data/merged/data.yaml.

Splits: a source's own test split is kept as test, so results stay comparable with the
source paper; everything else is re-split train/val 85/15 per source.

Video-derived sources (folder name matching `video_sources` in classes.yaml, e.g. egypt_*,
youtube_*, background_*) are split by VIDEO, not by frame: neighbouring frames of one drive are
near-copies, so splitting them by frame would leak the validation set into training.
With few videos, `video_val_share: 0` keeps every video in train: the public val set drives early
stopping, and the held-out test videos in data/videos/videos.csv measure the Egyptian result.
A frame's video is its file name without the trailing frame number (<video>_0042.jpg).

Background sources (folder name starting with background_) hold images with no hazards,
e.g. wet roads and streetlight reflections. They need no labels: every image gets an empty
label file, which teaches the model what is NOT a bump.

Usage:  python scripts/merge_datasets.py [--raw data/raw] [--out data/merged]
"""
import argparse, hashlib, json, random, re, shutil
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMG_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def load_scheme():
    cfg = yaml.safe_load((ROOT / 'configs/classes.yaml').read_text())
    names = [cfg['classes'][i] for i in sorted(cfg['classes'])]
    rules = [(names.index(k), re.compile(v, re.I)) for k, v in cfg['mapping'].items()]
    return names, rules, re.compile(cfg['exclude'], re.I)


def source_names(src: Path):
    if src.name.lower().startswith('background'):
        return []
    override = (yaml.safe_load((ROOT / 'configs/classes.yaml').read_text()).get('sources') or {}).get(src.name)
    if override:
        return list(override['names'])
    for y in list(src.rglob('data.yaml')) + list(src.rglob('*.yaml')):
        try:
            d = yaml.safe_load(y.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and 'names' in d:
            n = d['names']
            return [n[k] for k in sorted(n)] if isinstance(n, dict) else list(n)
    for t in src.rglob('classes.txt'):
        return [l.strip() for l in t.read_text().splitlines() if l.strip()]
    raise SystemExit(f'No data.yaml / classes.txt with class names found in {src}')


def build_map(names, rules, exclude):
    m = {}
    for i, n in enumerate(names):
        if exclude.search(n):
            continue
        for uid, rx in rules:
            if rx.search(n):
                m[i] = uid
                break
    return m


def dhash(path, size=8):
    with Image.open(path) as im:
        im = im.convert('L').resize((size + 1, size))
        px = im.tobytes()  # one byte per pixel in 'L' mode
    bits = ''.join('1' if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else '0'
                   for r in range(size) for c in range(size))
    return int(bits, 2)


def video_of(img: Path):
    return re.sub(r'_\d+$', '', img.stem)


def split_of(img: Path):
    parts = {p.lower() for p in img.parts}
    if 'test' in parts:
        return 'test'
    if 'valid' in parts or 'val' in parts:
        return 'val'
    return 'train'


def label_for(img: Path):
    # YOLO convention: .../images/x.jpg <-> .../labels/x.txt ; fall back to same folder
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == 'images':
            parts[i] = 'labels'
            cand = Path(*parts).with_suffix('.txt')
            if cand.exists():
                return cand
    cand = img.with_suffix('.txt')
    return cand if cand.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', default=ROOT / 'data/raw', type=Path)
    ap.add_argument('--out', default=ROOT / 'data/merged', type=Path)
    ap.add_argument('--seed', default=0, type=int)
    args = ap.parse_args()
    random.seed(args.seed)
    names, rules, exclude = load_scheme()
    cfg = yaml.safe_load((ROOT / 'configs/classes.yaml').read_text())
    video_rx = re.compile(cfg.get('video_sources', '$^'), re.I)
    video_val_share = float(cfg.get('video_val_share', 0))

    if args.out.exists():
        shutil.rmtree(args.out)
    for s in ('train', 'val', 'test'):
        (args.out / s / 'images').mkdir(parents=True)
        (args.out / s / 'labels').mkdir(parents=True)

    seen = {}
    report = {'sources': {}, 'duplicates_removed': 0}
    counts = defaultdict(Counter)

    for src in sorted(p for p in args.raw.iterdir() if p.is_dir()):
        src_names = source_names(src)
        cmap = build_map(src_names, rules, exclude)
        imgs = sorted(p for p in src.rglob('*') if p.suffix.lower() in IMG_EXT)
        rep = {'source_classes': src_names, 'mapping': {src_names[k]: names[v] for k, v in cmap.items()},
               'images': 0, 'kept': 0, 'dropped_boxes': 0}
        pending = []
        for img in imgs:
            rep['images'] += 1
            try:
                h = dhash(img)
            except Exception:
                continue
            if h in seen:
                report['duplicates_removed'] += 1
                continue
            seen[h] = img
            lab = label_for(img)
            lines = []
            if lab:
                for ln in lab.read_text().splitlines():
                    f = ln.split()
                    if len(f) < 5:
                        continue
                    cid = int(float(f[0]))
                    if cid in cmap:
                        lines.append(' '.join([str(cmap[cid])] + f[1:5]))
                    else:
                        rep['dropped_boxes'] += 1
            pending.append((img, lines, split_of(img)))

        # re-split non-test images 85/15 per source (per video for video-derived sources)
        rest = [x for x in pending if x[2] != 'test']
        if video_rx.search(src.name):
            # choose val videos by a hash of the video name, so one video lands on the same side in
            # every zip it appears in (Y frames, backgrounds, CVAT fixes)
            val = lambda i: int(hashlib.md5(video_of(i).encode()).hexdigest(), 16) % 1000 < 1000 * video_val_share
            final = [(i, l, 'val' if val(i) else 'train') for i, l, _ in rest]
            vids = {video_of(i) for i, _, _ in rest}
            rep['videos'] = {'train': sorted(v for v in vids if not val(Path(v + '_0'))),
                             'val': sorted(v for v in vids if val(Path(v + '_0')))}
        else:
            random.shuffle(rest)
            n_val = round(0.15 * len(rest))
            final = [(i, l, 'val') for i, l, _ in rest[:n_val]] + [(i, l, 'train') for i, l, _ in rest[n_val:]]
        final += [x for x in pending if x[2] == 'test']

        tag = re.sub(r'[^a-z0-9]+', '_', src.name.lower())
        for img, lines, split in final:
            stem = f'{tag}_{hashlib.md5(str(img).encode()).hexdigest()[:10]}'
            shutil.copy2(img, args.out / split / 'images' / f'{stem}{img.suffix.lower()}')
            (args.out / split / 'labels' / f'{stem}.txt').write_text('\n'.join(lines) + ('\n' if lines else ''))
            for l in lines:
                counts[split][names[int(l.split()[0])]] += 1
            counts[split]['images'] += 1
            rep['kept'] += 1
        report['sources'][src.name] = rep

    (args.out / 'data.yaml').write_text(yaml.safe_dump({
        'path': str(args.out), 'train': 'train/images', 'val': 'val/images', 'test': 'test/images',
        'names': {i: n for i, n in enumerate(names)}}, sort_keys=False))
    report['counts'] = {k: dict(v) for k, v in counts.items()}
    (args.out / 'merge_report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
