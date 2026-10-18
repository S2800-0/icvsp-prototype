"""Turn a corrected CVAT task into training zips, keeping the frame tags the YOLO export drops.

CVAT's *Ultralytics YOLO Detection 1.0* export has the boxes but loses frame tags (`unsure`, `debris`) and box
attributes (`subtype`), and has no label file for a frame with no boxes. So each task is exported twice (see
LABELLING_POLICY.md, section 3) and this script combines them:

  youtube_<name>.zip      frames with boxes (images + YOLO labels)
  background_<name>.zip   frames left empty on purpose: "no hazard here" (images only)
  <name>_subtypes.csv     one row per box: frame, class, subtype (to score each kind separately)

Left out: frames tagged `unsure`, and (with --skip-empty) frames that came from tag mode and were left empty: the
hazard was tagged in the video but is not visible, so an empty label would teach the model to ignore it.
Frames tagged `debris` are listed and kept (their other boxes still count).

Usage:
  python scripts/package_cvat.py --yolo cvat_yolo.zip --xml cvat_images.zip --images data/label_queue_b7/by_labeller \\
      --name youtube_batch7 [--skip-empty data/label_queue_b7/tags]
"""
import argparse
import csv
import io
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_yolo(path: Path) -> tuple[dict[str, str], dict]:
    """{frame stem: label text} for frames with boxes, and the class names."""
    labels, names = {}, None
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.endswith('data.yaml'):
                names = yaml.safe_load(z.read(n))['names']
            elif n.endswith('.txt') and '/labels/' in f'/{n}' and z.getinfo(n).file_size:
                labels[Path(n).stem] = z.read(n).decode()
    return labels, names


def read_cvat_xml(path: Path) -> tuple[dict[str, set], list[tuple[str, str, str]]]:
    """{frame stem: frame tags} and (frame, class, subtype) per box, from a CVAT for images 1.1 export."""
    data = path.read_bytes()
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read(next(n for n in z.namelist() if n.endswith('.xml')))
    tags, boxes = {}, []
    for img in ET.fromstring(data).iter('image'):
        stem = Path(img.get('name')).stem
        tags[stem] = {t.get('label') for t in img.findall('tag')}
        for b in img.findall('box'):
            sub = next((a.text for a in b.findall('attribute') if a.get('name') == 'subtype'), '') or ''
            boxes.append((stem, b.get('label'), sub))
    return tags, boxes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yolo', required=True, type=Path, help='CVAT export, Ultralytics YOLO Detection 1.0')
    ap.add_argument('--xml', required=True, type=Path, help='CVAT export, CVAT for images 1.1 (zip or .xml)')
    ap.add_argument('--images', required=True, type=Path, help='folder with the task\'s frames (searched recursively)')
    ap.add_argument('--name', required=True, help='youtube_<batch> or egypt_<batch>')
    ap.add_argument('--skip-empty', type=Path, help='folder of tag-mode frames: leave them out when left empty')
    ap.add_argument('--out', default=ROOT / 'data/reviewed', type=Path)
    a = ap.parse_args()
    if not a.name.startswith(('youtube_', 'egypt_')):
        raise SystemExit('--name must start with youtube_ or egypt_ (the merge uses it to split by video)')

    labels, names = read_yolo(a.yolo)
    tags, boxes = read_cvat_xml(a.xml)
    images = {p.stem: p for p in a.images.rglob('*.jpg')}
    tag_mode = {p.stem for p in a.skip_empty.rglob('*.jpg')} if a.skip_empty else set()
    missing = sorted(set(tags) - set(images))
    if missing:
        raise SystemExit(f'{len(missing)} frames of the task are not in {a.images}, e.g. {missing[:3]}')

    unsure = {s for s, t in tags.items() if 'unsure' in t}
    debris = sorted(s for s, t in tags.items() if 'debris' in t)
    boxed = sorted(s for s in tags if s in labels and s not in unsure)
    empty = sorted(s for s in tags if s not in labels and s not in unsure and s not in tag_mode)
    skipped = sorted(s for s in tags if s not in labels and s not in unsure and s in tag_mode)

    a.out.mkdir(parents=True, exist_ok=True)
    tail = a.name.split('_', 1)[1]
    with zipfile.ZipFile(a.out / f'{a.name}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for s in boxed:
            z.write(images[s], f'images/train/{s}.jpg')
            z.writestr(f'labels/train/{s}.txt', labels[s])
        z.writestr('data.yaml', yaml.safe_dump({'path': '.', 'train': 'images/train', 'names': names}, sort_keys=False))
        z.writestr('train.txt', ''.join(f'images/train/{s}.jpg\n' for s in boxed))
    with zipfile.ZipFile(a.out / f'background_{tail}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for s in empty:
            z.write(images[s], f'images/train/{s}.jpg')
    with open(a.out / f'{a.name}_subtypes.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['frame', 'class', 'subtype'])
        w.writerows(b for b in boxes if b[0] not in unsure)

    by_sub = {}
    for frame, cls, sub in boxes:
        if frame in unsure:
            continue
        by_sub[f'{cls}/{sub or "?"}'] = by_sub.get(f'{cls}/{sub or "?"}', 0) + 1
    print(f'{a.name}.zip: {len(boxed)} frames with boxes; background_{tail}.zip: {len(empty)} empty frames')
    print(f'left out: {len(unsure)} unsure, {len(skipped)} empty tag-mode frames; debris tagged on {len(debris)} frames')
    print('boxes by subtype:', ', '.join(f'{k} {v}' for k, v in sorted(by_sub.items())) or 'none')
    if any(sub == '' for _, _, sub in boxes):
        print('note: some boxes have no subtype; set the attribute in CVAT (LABELLING_POLICY.md, section 2.3)')


if __name__ == '__main__':
    main()
