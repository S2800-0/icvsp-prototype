"""Merge YOLO-format road-hazard datasets into one dataset with the unified ICVSP classes.

For every source folder under data/raw/<source>/ that contains a data.yaml (or a classes.txt),
the script:
  1. remaps source class IDs to the unified scheme in configs/classes.yaml,
  2. drops boxes whose class does not map (e.g. rumble strips, cracks),
  3. removes near-duplicate images across all sources (perceptual dHash),
  4. writes data/merged/{train,val,test}/{images,labels} and data/merged/data.yaml.

Splits: only the sources named by `test_sources` in classes.yaml (SBP-YOLO) keep their own test split, so the
public test set stays exactly the same when datasets are added; every other image is re-split train/val 85/15
per source. Test images are hashed first, and any other image that is an exact or near copy of a test image
(dHash Hamming distance <= 4) in an added dataset is removed, so no test image can leak into training through
another dataset. (Near copies inside the test source's own train split are kept, so the baseline stays the same,
and counted in the report.)
`max_images` under a source in classes.yaml caps how many images it contributes (images with speed bumps,
the scarcer class, are kept first).

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


def source_names(src: Path, src_cfg: dict):
    if src.name.lower().startswith('background'):
        return []
    override = src_cfg.get(src.name)
    if override and 'names' in override:
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


def near_test(h: int, test_hashes: list, max_dist: int = 4) -> bool:
    return any((h ^ t).bit_count() <= max_dist for t in test_hashes)


def video_of(img: Path):
    return re.sub(r'_\d+$', '', img.stem)


def split_of(img: Path):
    parts = {p.lower() for p in img.parts}
    if 'test' in parts:
        return 'test'
    if 'valid' in parts or 'val' in parts:
        return 'val'
    return 'train'


def yolo_box(v: list[str]) -> list[str]:
    """A YOLO box (cx cy w h) from a label line's coordinates; a polygon (x1 y1 x2 y2 x3 y3 ..., as in some
    Roboflow exports) becomes its bounding box instead of being misread as a box."""
    if len(v) == 4:
        return v
    xs, ys = [float(x) for x in v[0::2]], [float(y) for y in v[1::2]]
    x0, x1, y0, y1 = max(0.0, min(xs)), min(1.0, max(xs)), max(0.0, min(ys)), min(1.0, max(ys))
    return [f'{(x0 + x1) / 2:.6f}', f'{(y0 + y1) / 2:.6f}', f'{x1 - x0:.6f}', f'{y1 - y0:.6f}']


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
    ap.add_argument('--profile', default='', help='a named set of per-source overrides under `profiles` in classes.yaml')
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

    test_rx = re.compile(cfg.get('test_sources', '^sbp_yolo$'), re.I)
    src_cfg = {k: dict(v or {}) for k, v in (cfg.get('sources') or {}).items()}
    if args.profile:   # e.g. v5: keep only some images of a source
        for k, v in (cfg.get('profiles') or {})[args.profile].items():
            src_cfg.setdefault(k, {}).update(v or {})
    sources = sorted(p for p in args.raw.iterdir() if p.is_dir())
    images = {src: sorted(p for p in src.rglob('*') if p.suffix.lower() in IMG_EXT) for src in sources}

    # pass 1: hash the test images first, so a copy in another dataset can never displace or leak them
    seen, test_hashes = {}, []
    for src in sources:
        if not test_rx.search(src.name):
            continue
        for img in images[src]:
            if split_of(img) == 'test':
                try:
                    h = dhash(img)
                except Exception:
                    continue
                if h not in seen:
                    seen[h] = img
                    test_hashes.append(h)

    report = {'sources': {}, 'duplicates_removed': 0, 'near_test_copies_removed': 0}
    counts = defaultdict(Counter)

    for src in sources:
        src_names = source_names(src, src_cfg)
        only = set((src_cfg.get(src.name) or {}).get('only_images_with', []))   # source class names
        skip = set((src_cfg.get(src.name) or {}).get('skip_images_with', []))
        cmap = build_map(src_names, rules, exclude)
        imgs = images[src]
        keeps_test = bool(test_rx.search(src.name))
        rep = {'source_classes': src_names, 'mapping': {src_names[k]: names[v] for k, v in cmap.items()},
               'images': 0, 'kept': 0, 'dropped_boxes': 0}
        pending = []
        for img in imgs:
            rep['images'] += 1
            try:
                h = dhash(img)
            except Exception:
                continue
            is_test = keeps_test and split_of(img) == 'test'
            if h in seen and seen[h] != img:
                report['duplicates_removed'] += 1
                continue
            if not is_test and test_hashes and near_test(h, test_hashes):
                if not keeps_test:   # a copy of a test image in an added dataset: never train on it
                    report['near_test_copies_removed'] += 1
                    continue
                # inside the test source itself the split is the published one; kept so the baseline stays
                # identical, but counted so the overlap is known
                report.setdefault('near_test_within_test_source', 0)
                report['near_test_within_test_source'] += 1
            lab = label_for(img)
            lines, present = [], set()
            if lab:
                for ln in lab.read_text().splitlines():
                    f = ln.split()
                    if len(f) < 5:
                        continue
                    cid = int(float(f[0]))
                    present.add(src_names[cid] if cid < len(src_names) else str(cid))
                    if cid in cmap:
                        lines.append(' '.join([str(cmap[cid])] + yolo_box(f[1:])))
                    else:
                        rep['dropped_boxes'] += 1
            if (only and not present & only) or (skip and present & skip):
                rep['filtered_images'] = rep.get('filtered_images', 0) + 1
                continue
            seen[h] = img
            pending.append((img, lines, split_of(img) if keeps_test else 'train'))

        cap = (src_cfg.get(src.name) or {}).get('max_images')
        if cap and len(pending) > cap:
            rnd = random.Random(f'{args.seed}-{src.name}')
            rnd.shuffle(pending)
            bump = names.index('speed_bump')
            pending.sort(key=lambda x: not any(int(l.split()[0]) == bump for l in x[1]))  # stable: bumps first
            rep['capped_from'] = len(pending)
            pending = pending[:cap]

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
            # a fresh generator per source: its split must not depend on which other sources are present
            # (a shared one moved SBP images into validation that v2, the starting point of v4 / v5, had trained
            # on). Random(seed) reproduces the split of the SBP-only merges v0-v3c, which shuffled SBP first.
            random.Random(args.seed).shuffle(rest)
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
