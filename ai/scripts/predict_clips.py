"""Quick visual check: run the detector on the team's Egyptian phone clips.

Saves each clip with the model's boxes drawn on it, plus a per-clip summary of when it saw a
speed bump or pothole. This shows WHAT the model detects, but it is not a measurement:
it cannot tell you what the model missed. For recall/precision numbers, label the frames
and use evaluate.py (README, step 3).

Usage:  python scripts/predict_clips.py --weights runs/v1_e150/weights/best.pt [--conf 0.25]
"""
import argparse, csv
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
VIDEO = {'.mov', '.mp4', '.m4v', '.avi'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--clips', default=ROOT / 'data/egypt_test/clips', type=Path)
    ap.add_argument('--out', default=ROOT / 'runs/egypt_clips', type=Path)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--device', default='0' if torch.cuda.is_available() else
                    'mps' if torch.backends.mps.is_available() else 'cpu')
    a = ap.parse_args()

    model = YOLO(a.weights)
    rows = []
    for clip in sorted(p for p in a.clips.iterdir() if p.suffix.lower() in VIDEO):
        hits = {n: [] for n in model.names.values()}
        cap = cv2.VideoCapture(str(clip)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0; cap.release()
        frames = 0
        for r in model.predict(source=str(clip), conf=a.conf, imgsz=a.imgsz, device=a.device, stream=True,
                               save=True, project=str(a.out), name=clip.stem, exist_ok=True, verbose=False):
            t = frames / fps
            for c, s in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                hits[model.names[int(c)]].append((t, s))
            frames += 1
        for cls, h in hits.items():
            rows.append({'clip': clip.name, 'class': cls, 'frames_with_detection': len({round(t, 3) for t, _ in h}),
                         'first_seen_s': round(min(t for t, _ in h), 1) if h else '',
                         'last_seen_s': round(max(t for t, _ in h), 1) if h else '',
                         'max_confidence': round(max(s for _, s in h), 2) if h else ''})
        seen = ', '.join(f'{c}: {len(h)} boxes' for c, h in hits.items())
        print(f'{clip.name}: {frames} frames ({frames / fps:.0f} s) -> {seen}')
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / 'summary.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['clip'])
        w.writeheader(); w.writerows(rows)
    print(f'\nAnnotated videos and summary.csv saved in {a.out}')


if __name__ == '__main__':
    main()
