"""Visual check: draw each model's boxes on the frames just before every listed event.

Timing-based scoring (event_recall.py) can count a box that fires near an event even when it
sits on the sky or the dashboard. This script shows where the boxes actually are: for each
event it takes the frames 3, 2, 1 and 0 s before it, runs every model on them, and saves one
contact sheet per event (rows = models, columns = seconds before the event).

Usage:  python scripts/check_events.py CLIP EVENTS.csv --models models/v1_e150.pt:640 models/v2_e150_960.pt:960
"""
import argparse, csv, subprocess, tempfile
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def secs(mmss):
    m, s = mmss.replace(' ', '').split(':')
    return int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('clip', type=Path)
    ap.add_argument('events', type=Path)
    ap.add_argument('--models', nargs='+', default=['models/v1_e150.pt:640', 'models/v2_e150_960.pt:960'])
    ap.add_argument('--offsets', default='3,2,1,0', help='seconds before each event')
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--out', type=Path, default=ROOT / 'runs/event_check')
    a = ap.parse_args()
    dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
    models = [(Path(m.split(':')[0]).stem, YOLO(m.split(':')[0]), int(m.split(':')[1])) for m in a.models]
    offsets = [float(x) for x in a.offsets.split(',')]
    a.out.mkdir(parents=True, exist_ok=True)

    times = {}
    for r in csv.DictReader(open(a.events)):
        if r['clip'] == a.clip.name:
            times.setdefault(r['time'], []).append(r['type'])
    tmp = Path(tempfile.mkdtemp())
    TW = 640
    for label, types in times.items():
        t0 = secs(label)
        tiles, notes = [], []
        for name, model, sz in models:
            row = []
            for off in offsets:
                png = tmp / 'f.png'
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0, t0 - off):.3f}', '-i', str(a.clip),
                                '-frames:v', '1', str(png)], check=True)
                r = model.predict(str(png), imgsz=sz, conf=a.conf, device=dev, verbose=False)[0]
                im = Image.fromarray(r.plot(line_width=3)[:, :, ::-1])
                row.append(im.resize((TW, int(im.height * TW / im.width))))
                notes.append(f"{name} -{off:g}s: " + (', '.join(f'{model.names[int(c)]} {float(s):.2f}'
                                                               for c, s in zip(r.boxes.cls, r.boxes.conf)) or '-'))
            tiles.append(row)
        th = tiles[0][0].height
        sheet = Image.new('RGB', (TW * len(offsets), (th + 26) * len(models) + 30), 'white')
        d = ImageDraw.Draw(sheet)
        d.text((8, 8), f'{a.clip.name} · event {label} ({", ".join(types)}) · columns: {", ".join(f"-{o:g}s" for o in offsets)}', fill='black')
        for i, row in enumerate(tiles):
            y = 30 + i * (th + 26)
            d.text((8, y + 6), models[i][0], fill='black')
            for j, im in enumerate(row):
                sheet.paste(im, (j * TW, y + 26))
        out = a.out / f'{a.clip.stem}_{label.replace(":", "m")}.jpg'
        sheet.save(out, quality=85)
        print(f'{label} ({", ".join(types)})')
        for n in notes:
            print('   ', n)


if __name__ == '__main__':
    main()
