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

Tag mode (--events): instead of scanning, take the frames just BEFORE each hazard the team tagged
while watching the video in tools/review.html (B = bump, H = pothole; "Export events.csv").
That is where the bump is in view, so this is the fastest way to collect real bumps, including the
ones the model misses today. Default: 3 frames per tag, 2.5, 1.5 and 0.5 s before it.

Hard-negative mode: --classes speed_bump --avoid-events data/tags/*.csv --random-per-min 0 keeps only
frames where the model draws a speed-bump box, away from every hazard the team tagged. Most of these
are kerbs, buses or cars taken for bumps; sorted N in tools/review.html they become "not a bump".

Frames are saved as data/label_queue/<video_id>/<video_id>_<frame>.jpg; the name before the last
underscore is what merge_datasets.py uses to keep each video in a single split.
Then:  python scripts/prelabel.py --weights models/v2_e150_960.pt --imgsz 960 --frames data/label_queue

Usage:  python scripts/harvest_frames.py [--weights models/v2_e150_960.pt] [--every 1.0] [--max 250]
        python scripts/harvest_frames.py --events data/tags/*.csv --out data/label_queue_tags
"""
import argparse, csv, random
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def secs(mmss):
    m, s = mmss.replace(' ', '').split(':')
    return int(m) * 60 + float(s)


def save_frames(path: Path, video_id: str, idxs, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    cap, n = cv2.VideoCapture(str(path)), 0
    for idx in sorted(set(idxs)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, img = cap.read()
        if ok:
            cv2.imwrite(str(out / f"{video_id}_{idx:06d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92]); n += 1
    cap.release()
    return n


def from_tags(a, videos):
    by_file = {Path(v['file']).stem: v for v in videos}
    tags = {}
    for f in a.events:
        for r in csv.DictReader(open(f)):
            v = by_file.get(Path(r['clip']).stem)
            if v is None:
                print(f"{f.name}: {r['clip']} is not in {a.manifest.name}, skipped"); continue
            if v['split'].strip().lower() != 'train':
                print(f"{f.name}: {r['clip']} is a {v['split']} video, never used for training"); continue
            tags.setdefault(v['video_id'], (v, []))[1].append((secs(r['time']), r['type']))
    offsets = [float(x) for x in a.offsets.split(',')]
    total = 0
    for vid, (v, ts) in tags.items():
        path = a.manifest.parent / v['file']
        cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
        idxs = [round((t + o) * fps) for t, _ in ts for o in offsets if 0 <= round((t + o) * fps) < n_frames]
        n = save_frames(path, vid, idxs, a.out / vid)
        kinds = ', '.join(f'{k} {sum(1 for _, x in ts if x == k)}' for k in sorted({x for _, x in ts}))
        print(f'{vid}: {len(ts)} tags ({kinds}) -> {n} frames in {a.out / vid}', flush=True)
        total += n
    print(f'\n{total} frames. Next: python scripts/prelabel.py --weights {a.weights} --imgsz {a.imgsz} '
          f'--frames {a.out} --out data/egypt_test/prelabels_tags')


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
    ap.add_argument('--events', nargs='*', type=Path, help='tag mode: events CSVs exported from tools/review.html')
    ap.add_argument('--classes', nargs='*', help='count a frame as "fired" only for these classes (default: all)')
    ap.add_argument('--avoid-events', nargs='*', type=Path, default=[],
                    help='skip frames from 4 s before to 3 s after any event in these CSVs (real hazards)')
    ap.add_argument('--offsets', default='-2.5,-1.5,-0.5', help='tag mode: seconds around each tag')
    ap.add_argument('--device', default='0' if torch.cuda.is_available() else
                    'mps' if torch.backends.mps.is_available() else 'cpu')
    a = ap.parse_args()
    random.seed(0)

    videos = list(csv.DictReader(open(a.manifest)))
    if a.events:
        return from_tags(a, videos)
    model = YOLO(str(a.weights))
    avoid = {}
    for f in a.avoid_events:
        for r in csv.DictReader(open(f)):
            t = secs(r['time'])
            avoid.setdefault(Path(r['clip']).stem, []).append((t - 4, t + 3))
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
            idx = k * stride
            if any(lo <= idx / fps <= hi for lo, hi in avoid.get(Path(v['file']).stem, [])):
                continue
            hit = [model.names[int(c)] for c in r.boxes.cls.tolist()]
            if a.classes:
                hit = [h for h in hit if h in a.classes]
            (fired if hit else quiet).append(idx)   # frame numbers only, to save memory
        minutes = n_frames / fps / 60
        n_rand = min(len(quiet), round(a.random_per_min * minutes))
        if len(fired) > a.max - n_rand:      # too many: keep an even spread over the video
            step = len(fired) / (a.max - n_rand)
            fired = [fired[int(i * step)] for i in range(a.max - n_rand)]
        keep = sorted(fired + random.sample(quiet, n_rand))
        out = a.out / v['video_id']
        save_frames(path, v['video_id'], keep, out)
        print(f"{v['video_id']}: {minutes:.1f} min, {len(fired)} frames where the model fired + {n_rand} random "
              f"-> {len(keep)} frames in {out}", flush=True)
    print(f'\nNext: python scripts/prelabel.py --weights {a.weights} --imgsz {a.imgsz} --frames {a.out}')


if __name__ == '__main__':
    main()
