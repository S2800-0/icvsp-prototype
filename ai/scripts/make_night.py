"""Add synthetic night copies of daytime training images to the merged dataset.

Almost all public pothole / speed-bump images are daytime. For a share of the daytime TRAINING
images (never val or test), this writes a night version next to the original, with the same labels:

  - darker image with a sodium-orange or LED-blue cast, black sky, brighter near the headlights,
  - wet-road reflections of lamps and tail lights (vertical coloured streaks) and a glossy band
    across the road, always AWAY from the labelled boxes. They carry no label, so the model learns
    that a bright streak is not a speed bump (the failure seen on the Cairo night video at 4:03),
  - sensor noise and slight blur, as in a phone or dashcam at night.

Images that are already dark (mean brightness below --dark) are left alone.
Night copies are named <original>_night.jpg, so a re-run replaces them instead of adding more.

Usage:  python scripts/make_night.py [--data data/merged] [--share 0.3]
        python scripts/make_night.py --preview 12     # writes a contact sheet to check the look
"""
import argparse, random
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]


def boxes_of(label: Path, w, h):
    out = []
    if label.exists():
        for ln in label.read_text().splitlines():
            f = ln.split()
            if len(f) >= 5:
                x, y, bw, bh = (float(v) for v in f[1:5])
                out.append(((x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h))
    return out


def _free(x0, y0, x1, y1, boxes):
    return not any(x0 < bx1 and x1 > bx0 and y0 < by1 and y1 > by0 for bx0, by0, bx1, by1 in boxes)


def night(img: Image.Image, boxes, rng: random.Random):
    """Modelled on the Cairo night video: lit streets, dark sky, glossy wet road."""
    a = np.asarray(img.convert('RGB')).astype(np.float32) / 255
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    a = a ** rng.uniform(1.4, 2.0) * rng.uniform(0.45, 0.75)            # darker, crushed shadows
    a *= np.array([0.95, 0.9, 0.85]) if rng.random() < 0.6 else np.array([0.85, 0.92, 1.05])  # sodium or LED light
    sky = np.clip((0.45 * h - yy) / (0.25 * h), 0, 1)[..., None]         # upper part: sky goes black
    a *= 1 - 0.8 * sky
    d = np.sqrt(((xx - w / 2) / (0.9 * w)) ** 2 + ((yy - 1.1 * h) / (1.0 * h)) ** 2)
    a *= np.clip(1.3 - d, 0.35, 1.0)[..., None]                          # headlights: brighter near the car
    colours = [np.array([1.0, 0.65, 0.3]), np.array([1.0, 0.25, 0.2]), np.array([1.0, 0.95, 0.85])]
    for _ in range(rng.randint(1, 5)):                                   # lamp / tail-light reflections:
        c = rng.choice(colours) * rng.uniform(0.3, 0.7)                  # vertical streaks on the road
        sw, sh = rng.uniform(0.01, 0.035) * w, rng.uniform(0.12, 0.35) * h
        for _try in range(10):
            x0, y0 = rng.uniform(0, w - sw), rng.uniform(0.55 * h, h - 0.5 * sh)
            if _free(x0, y0, x0 + sw, y0 + sh, boxes):
                a += np.exp(-(((xx - x0 - sw / 2) / (sw / 2)) ** 2 + ((yy - y0 - sh / 2) / (sh / 2)) ** 2))[..., None] * c
                break
    if rng.random() < 0.6:                                               # glossy wet band across the road,
        sw, sh = rng.uniform(0.25, 0.6) * w, rng.uniform(0.03, 0.08) * h # where the false bump boxes landed
        for _try in range(10):
            x0, y0 = rng.uniform(0, w - sw), rng.uniform(0.55 * h, 0.9 * h)
            if _free(x0, y0, x0 + sw, y0 + sh, boxes):
                g = np.exp(-(((xx - x0 - sw / 2) / (sw / 2)) ** 4 + ((yy - y0 - sh / 2) / (sh / 2)) ** 2))
                a += g[..., None] * rng.choice(colours) * rng.uniform(0.08, 0.2)
                break
    a += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(0.01, 0.025), a.shape)
    out = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=ROOT / 'data/merged', type=Path)
    ap.add_argument('--share', type=float, default=0.3, help='share of daytime train images that get a night copy')
    ap.add_argument('--dark', type=float, default=70, help='skip images whose mean brightness (0-255) is below this')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--preview', type=int, default=0, help='only write a contact sheet of this many examples')
    a = ap.parse_args()
    rng = random.Random(a.seed)

    img_dir, lab_dir = a.data / 'train/images', a.data / 'train/labels'
    imgs = sorted(p for p in img_dir.iterdir() if '_night' not in p.stem and not p.stem.startswith('background'))
    rng.shuffle(imgs)
    picked = imgs[:a.preview] if a.preview else imgs[:round(a.share * len(imgs))]

    made, skipped, sheet = 0, 0, []
    for p in picked:
        im = Image.open(p).convert('RGB')
        if np.asarray(im.convert('L')).mean() < a.dark:
            skipped += 1
            continue
        lab = lab_dir / f'{p.stem}.txt'
        n = night(im, boxes_of(lab, *im.size), rng)
        if a.preview:
            sheet.append((im, n))
            continue
        n.save(img_dir / f'{p.stem}_night.jpg', quality=90)
        (lab_dir / f'{p.stem}_night.txt').write_text(lab.read_text() if lab.exists() else '')
        made += 1

    if a.preview:
        W = 480
        rows = [(o.resize((W, int(o.height * W / o.width))), n.resize((W, int(n.height * W / n.width)))) for o, n in sheet]
        H = sum(r[0].height for r in rows)
        out = Image.new('RGB', (2 * W, H), 'white')
        y = 0
        for o, n in rows:
            out.paste(o, (0, y)); out.paste(n, (W, y)); y += o.height
        dest = ROOT / 'runs/night_preview.jpg'
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest, quality=85)
        print(f'Preview (left: original, right: night copy) saved to {dest}')
        return
    print(f'{made} night copies added to {img_dir} ({skipped} already-dark images skipped)')


if __name__ == '__main__':
    main()
