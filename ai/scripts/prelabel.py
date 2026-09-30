"""Pre-label the Egyptian frames with the model, so labelling means correcting boxes, not drawing them.

Reads every frame under data/egypt_test/frames/, runs the detector, and writes an
Ultralytics-YOLO dataset that CVAT can import ("Ultralytics YOLO Detection 1.0"):

  data/egypt_test/prelabels/
    data.yaml
    images/train/<frame>.jpg
    labels/train/<frame>.txt     (empty file = model saw nothing; still check the frame)

Usage:  python scripts/prelabel.py --weights models/v1_e150.pt [--conf 0.25]
"""
import argparse, shutil
from pathlib import Path

import torch
import yaml
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--frames', default=ROOT / 'data/egypt_test/frames', type=Path)
    ap.add_argument('--out', default=ROOT / 'data/egypt_test/prelabels', type=Path)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--device', default='0' if torch.cuda.is_available() else
                    'mps' if torch.backends.mps.is_available() else 'cpu')
    a = ap.parse_args()

    frames = sorted(a.frames.rglob('*.jpg'))
    if not frames:
        raise SystemExit(f'No frames in {a.frames}. Run scripts/extract_frames.py first.')
    if a.out.exists():
        shutil.rmtree(a.out)
    img_dir, lab_dir = a.out / 'images/train', a.out / 'labels/train'
    img_dir.mkdir(parents=True)
    lab_dir.mkdir(parents=True)

    model = YOLO(a.weights)
    boxes = {n: 0 for n in model.names.values()}
    for f in frames:
        shutil.copy2(f, img_dir / f.name)
        r = model.predict(source=str(f), conf=a.conf, imgsz=a.imgsz, device=a.device, verbose=False)[0]
        lines = []
        for c, (x, y, w, h) in zip(r.boxes.cls.tolist(), r.boxes.xywhn.tolist()):
            lines.append(f'{int(c)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}')
            boxes[model.names[int(c)]] += 1
        (lab_dir / f'{f.stem}.txt').write_text('\n'.join(lines) + ('\n' if lines else ''))

    (a.out / 'data.yaml').write_text(yaml.safe_dump(
        {'path': '.', 'train': 'images/train', 'names': dict(model.names)}, sort_keys=False))
    (a.out / 'train.txt').write_text(''.join(f'images/train/{f.name}\n' for f in frames))
    print(f'{len(frames)} frames pre-labelled: ' + ', '.join(f'{n} {k}' for n, k in boxes.items()))
    print(f'Import {a.out} into CVAT as "Ultralytics YOLO Detection 1.0", correct every box, add the missed ones.')


if __name__ == '__main__':
    main()
