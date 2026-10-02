"""Quick visual check: run the detector on the team's Egyptian phone clips.

Saves each clip with the model's boxes drawn on it, plus a per-clip summary of when it saw a
speed bump or pothole. This shows WHAT the model detects, but it is not a measurement:
it cannot tell you what the model missed. For recall/precision numbers, label the frames
and use evaluate.py (README, step 3).

Also writes detections.csv (one row per box: clip, time, class, confidence, box corners as
fractions of the frame), which event_recall.py matches against the team's list of bumps and
potholes, and tools/review.html draws over the video.

Usage:  python scripts/predict_clips.py --weights models/v1_e150.pt [--conf 0.25] [--stride 2]
"""
import argparse, csv
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

import espdet_support

espdet_support.enable()   # lets ESPDet-Pico weights load; no effect otherwise

ROOT = Path(__file__).resolve().parents[1]
VIDEO = {'.mov', '.mp4', '.m4v', '.avi'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--clips', default=ROOT / 'data/egypt_test/clips', type=Path)
    ap.add_argument('--out', default=ROOT / 'runs/egypt_clips', type=Path)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--stride', type=int, default=1, help='check every n-th frame (2 = half the frames)')
    ap.add_argument('--no-video', action='store_true', help='skip the annotated video copy (faster; review.html draws the boxes)')
    ap.add_argument('--device', default='0' if torch.cuda.is_available() else
                    'mps' if torch.backends.mps.is_available() else 'cpu')
    a = ap.parse_args()

    model = YOLO(a.weights)
    rows, dets = [], []
    for clip in sorted(p for p in a.clips.iterdir() if p.suffix.lower() in VIDEO):
        hits = {n: [] for n in model.names.values()}
        cap = cv2.VideoCapture(str(clip)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0; cap.release()
        frames = 0
        for r in model.predict(source=str(clip), conf=a.conf, imgsz=a.imgsz, device=a.device, stream=True,
                               vid_stride=a.stride, save=not a.no_video, project=str(a.out), name=clip.stem,
                               exist_ok=True, verbose=False):
            t = frames * a.stride / fps
            for c, s, (x1, y1, x2, y2) in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxyn.tolist()):
                hits[model.names[int(c)]].append((t, s))
                dets.append({'clip': clip.name, 't_s': round(t, 3), 'class': model.names[int(c)], 'conf': round(s, 3),
                             'x1': round(x1, 4), 'y1': round(y1, 4), 'x2': round(x2, 4), 'y2': round(y2, 4)})
            frames += 1
        for cls, h in hits.items():
            rows.append({'clip': clip.name, 'class': cls, 'frames_with_detection': len({round(t, 3) for t, _ in h}),
                         'first_seen_s': round(min(t for t, _ in h), 1) if h else '',
                         'last_seen_s': round(max(t for t, _ in h), 1) if h else '',
                         'max_confidence': round(max(s for _, s in h), 2) if h else ''})
        seen = ', '.join(f'{c}: {len(h)} boxes' for c, h in hits.items())
        print(f'{clip.name}: {frames} frames checked ({frames * a.stride / fps:.0f} s) -> {seen}', flush=True)
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / 'summary.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['clip'])
        w.writeheader(); w.writerows(rows)
    with open(a.out / 'detections.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['clip', 't_s', 'class', 'conf', 'x1', 'y1', 'x2', 'y2'])
        w.writeheader(); w.writerows(dets)
    print(f'\nAnnotated videos, summary.csv and detections.csv saved in {a.out}')


if __name__ == '__main__':
    main()
