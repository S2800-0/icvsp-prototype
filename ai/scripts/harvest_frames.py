"""Pick the frames worth labelling from long driving videos (YouTube drives, team recordings).

Labelling every frame of a 10-minute drive is wasted effort: most frames show empty road, and
neighbouring frames are near-copies. This script scans each TRAINING video once per --every
seconds and keeps two kinds of frames:

  1. frames where the current model fires (conf >= --conf). After correction in CVAT these become
     either real hazards (positives) or, when the box is wrong and gets deleted, hard negatives:
     exactly the reflections and road markings the model confuses with bumps today.
  2. a random sample of the remaining frames (--random-per-min), so plain road is also covered
     and missed hazards can be added by hand.

Videos are listed in data/videos/videos.csv. A video marked split=test is never harvested, so the
held-out test videos stay unseen by training. Split by VIDEO, never by frame.

  video_id,file,split,time_of_day,place,source,notes
  cairo_yt_01,cairo_night_01.mp4,test,night,Cairo,<url>,events in data/egypt_test/external/events_cairo.csv
  cairo_yt_02,cairo_night_02.mp4,train,night,Cairo,<url>,

Frames are saved as data/label_queue/<video_id>/<video_id>_<frame>.jpg; the name before the last
underscore is what merge_datasets.py uses to keep each video in a single split.
Then:  python scripts/prelabel.py --weights models/v2_e150_960.pt --imgsz 960 --frames data/label_queue

Usage:  python scripts/harvest_frames.py [--weights models/v2_e150_960.pt] [--every 1.0] [--max 250]
"""
import argparse, csv, random
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default=ROOT / 'data/videos/videos.csv', type=Path)
    ap.add_argument('--weights', default=ROOT / 'models/v2_e150_960.pt')
    ap.add_argument('--imgsz', type=int, default=960)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--every', type=float, default=1.0, help='seconds between scanned frames')
    ap.add_argument('--random-per-min', type=float, default=4, help='extra random frames per minute of video')
    ap.add_argument('--max', type=int, default=250, help='most frames kept per video')
    ap.add_argument('--only', nargs='*', help='harvest only these video_ids')
    ap.add_argument('--out', default=ROOT / 'data/label_queue', type=Path)
    ap.add_argument('--device', default='0' if torch.cuda.is_available() else
                    'mps' if torch.backends.mps.is_available() else 'cpu')
    a = ap.parse_args()
    random.seed(0)

    videos = list(csv.DictReader(open(a.manifest)))
    model = YOLO(str(a.weights))
    for v in videos:
        if a.only and v['video_id'] not in a.only:
            continue
        if v['split'].strip().lower() != 'train':
            print(f"{v['video_id']}: split={v['split']}, not harvested")
            continue
        path = a.manifest.parent / v['file']
        cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
        stride = max(1, round(fps * a.every))
        fired, quiet = [], []
        for k, r in enumerate(model.predict(source=str(path), imgsz=a.imgsz, conf=a.conf, device=a.device,
                                            stream=True, vid_stride=stride, verbose=False)):
            (fired if len(r.boxes) else quiet).append(k * stride)   # frame numbers only, to save memory
        minutes = n_frames / fps / 60
        n_rand = min(len(quiet), round(a.random_per_min * minutes))
        if len(fired) > a.max - n_rand:      # too many: keep an even spread over the video
            step = len(fired) / (a.max - n_rand)
            fired = [fired[int(i * step)] for i in range(a.max - n_rand)]
        keep = sorted(fired + random.sample(quiet, n_rand))
        out = a.out / v['video_id']
        out.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(path))
        for idx in keep:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, img = cap.read()
            if ok:
                cv2.imwrite(str(out / f"{v['video_id']}_{idx:06d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        cap.release()
        print(f"{v['video_id']}: {minutes:.1f} min, {len(fired)} frames where the model fired + {n_rand} random "
              f"-> {len(keep)} frames in {out}", flush=True)
    print(f'\nNext: python scripts/prelabel.py --weights {a.weights} --imgsz {a.imgsz} --frames {a.out}')


if __name__ == '__main__':
    main()
