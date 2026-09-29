"""Train the ICVSP pothole / speed-bump detector on the merged dataset.

Defaults are sized for a 16 GB Apple-silicon laptop (MPS). On Colab, pass --device 0 --batch 32.

Usage:  python scripts/train.py [--model yolo11n.pt] [--epochs 50] [--fraction 1.0]
        --fraction < 1.0 trains on a random subset (used for the learning curve).
        --resume runs/<name>/weights/last.pt continues an interrupted run where it stopped
        (same dataset path and runs folder as the original run, e.g. a later Kaggle session).
"""
import argparse
from pathlib import Path

import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=ROOT / 'data/merged/data.yaml')
    ap.add_argument('--model', default='yolo11n.pt')
    ap.add_argument('--epochs', type=int, default=50)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--fraction', type=float, default=1.0)
    ap.add_argument('--device', default='mps' if torch.backends.mps.is_available() else 'cpu')
    ap.add_argument('--patience', type=int, default=15, help='stop early after this many epochs without improvement')
    ap.add_argument('--name', default=None)
    ap.add_argument('--resume', default=None, help="an interrupted run's last.pt; all other options come from it")
    a = ap.parse_args()

    if a.resume:
        YOLO(a.resume).train(resume=True)
        return

    name = a.name or f'{Path(a.model).stem}_e{a.epochs}_f{int(a.fraction * 100)}'
    YOLO(a.model).train(
        data=str(a.data), epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=a.device,
        fraction=a.fraction, project=str(ROOT / 'runs'), name=name, exist_ok=True,
        patience=a.patience, workers=4, seed=0, plots=True,
    )


if __name__ == '__main__':
    main()
