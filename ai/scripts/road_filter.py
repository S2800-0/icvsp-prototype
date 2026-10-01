"""Check every detection against the road surface, to drop boxes that are not on the road.

A speed bump or pothole is always ON the road. Many false bump boxes sit on kerbs, pavements,
buses and parked cars. A small segmentation model trained on Cityscapes (NVIDIA SegFormer-B0)
labels each pixel as road, sidewalk, car, bus, ...; this script measures, for every box in a
detections.csv (from predict_clips.py), the share of its pixels that are road and sidewalk, and
writes them as extra columns. event_recall.py can then score with or without the filter.

Usage:  python scripts/road_filter.py runs/day_v3b/detections.csv VIDEO.mp4 [--out ...]
"""
import argparse, csv
from pathlib import Path

import cv2
import numpy as np
import torch
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

MODEL = 'nvidia/segformer-b0-finetuned-cityscapes-1024-1024'
ROAD, SIDEWALK = 0, 1           # Cityscapes train ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('detections', type=Path)
    ap.add_argument('video', type=Path)
    ap.add_argument('--out', type=Path, default=None, help='default: <detections>_road.csv')
    ap.add_argument('--save-masks', type=Path, default=None, help='folder for a few overlay images (sanity check)')
    a = ap.parse_args()
    out = a.out or a.detections.with_name(a.detections.stem + '_road.csv')

    dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
    proc = SegformerImageProcessor.from_pretrained(MODEL)
    net = SegformerForSemanticSegmentation.from_pretrained(MODEL).to(dev).eval()

    rows = list(csv.DictReader(open(a.detections)))
    times = sorted({float(r['t_s']) for r in rows})
    cap = cv2.VideoCapture(str(a.video)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    masks = {}
    for i, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * fps))
        ok, bgr = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        with torch.no_grad():
            logits = net(**proc(images=rgb, return_tensors='pt').to(dev)).logits
        seg = torch.nn.functional.interpolate(logits, size=rgb.shape[:2], mode='bilinear').argmax(1)[0].cpu().numpy()
        masks[t] = seg
        if a.save_masks and i % max(1, len(times) // 8) == 0:
            a.save_masks.mkdir(parents=True, exist_ok=True)
            over = rgb.copy()
            over[seg == ROAD] = (0.5 * over[seg == ROAD] + [0, 110, 0]).astype(np.uint8)
            over[seg == SIDEWALK] = (0.5 * over[seg == SIDEWALK] + [120, 0, 120]).astype(np.uint8)
            cv2.imwrite(str(a.save_masks / f'{a.video.stem}_{t:07.2f}.jpg'), cv2.cvtColor(over, cv2.COLOR_RGB2BGR)[::2, ::2])
        if (i + 1) % 100 == 0:
            print(f'  {i + 1}/{len(times)} frames', flush=True)
    cap.release()

    for r in rows:
        seg = masks.get(float(r['t_s']))
        if seg is None or r.get('x1', '') == '':
            r['road_frac'] = r['sidewalk_frac'] = r['road_frac_ctx'] = ''
            continue
        h, w = seg.shape
        x1, y1, x2, y2 = (float(r[k]) for k in ('x1', 'y1', 'x2', 'y2'))
        box = seg[int(y1 * h):max(int(y2 * h), int(y1 * h) + 1), int(x1 * w):max(int(x2 * w), int(x1 * w) + 1)]
        r['road_frac'] = round(float((box == ROAD).mean()), 3)
        r['sidewalk_frac'] = round(float((box == SIDEWALK).mean()), 3)
        # the box enlarged by 50 % on every side: a pothole is itself a dark hole that the road model
        # may not call "road", and at night wet asphalt is patchy, so the surroundings are also checked
        bw, bh = x2 - x1, y2 - y1
        X1, Y1, X2, Y2 = max(0, x1 - bw / 2), max(0, y1 - bh / 2), min(1, x2 + bw / 2), min(1, y2 + bh / 2)
        ctx = seg[int(Y1 * h):max(int(Y2 * h), int(Y1 * h) + 1), int(X1 * w):max(int(X2 * w), int(X1 * w) + 1)]
        r['road_frac_ctx'] = round(float((ctx == ROAD).mean()), 3)
    with open(out, 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    print(f'{len(rows)} detections, {len(masks)} frames -> {out}')


if __name__ == '__main__':
    main()
