"""Keep only detections that move like a fixed object the car is approaching.

A speed bump or pothole is fixed to the road. As the car drives towards it, its box moves DOWN the
image and grows, until it disappears under the car. Most false alarms move differently:
  - kerbs, lane edges and road seams are continuous lines along the road, so every frame boxes a
    different piece of them at about the same place in the image: no steady downward movement;
  - buses, cars and reflections of their lights move with the traffic: they stay put or jump.

The script links detections of the same class in consecutive frames into tracks (nearest box
centre within --max-jump, gap at most --max-gap seconds) and marks a track as "approaching" when it
  * has at least --min-hits detections,
  * moves down by at least --min-drop of the image height between its first and last detection,
  * moves down at a rate of at least --min-rate image heights per second (least-squares slope), and
  * does not shrink (last box width >= first box width).
Thresholds were fixed before scoring. A stationary car makes real hazards look stationary too; on
the unit, GPS speed will be used to skip the check while the car is (nearly) stopped.

Writes <input>_motion.csv with extra columns track_id, track_hits, track_drop, approaching (1/0).

Usage:  python scripts/motion_check.py runs/day_v3c/detections_road.csv
"""
import argparse, csv
from collections import defaultdict
from pathlib import Path

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('detections', type=Path)
    ap.add_argument('--out', type=Path, default=None)
    ap.add_argument('--max-gap', type=float, default=0.4, help='seconds a track may go without a detection')
    ap.add_argument('--max-jump', type=float, default=0.12, help='largest centre move per link (fraction of the image)')
    ap.add_argument('--min-hits', type=int, default=3)
    ap.add_argument('--min-drop', type=float, default=0.03, help='total downward move (fraction of image height)')
    ap.add_argument('--min-rate', type=float, default=0.02, help='downward speed (image heights per second)')
    a = ap.parse_args()
    out = a.out or a.detections.with_name(a.detections.stem + '_motion.csv')

    rows = list(csv.DictReader(open(a.detections)))
    for r in rows:
        x1, y1, x2, y2 = (float(r[k]) for k in ('x1', 'y1', 'x2', 'y2'))
        r['_t'], r['_cx'], r['_cy'], r['_w'] = float(r['t_s']), (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1

    tracks = []                                     # list of lists of rows
    for cls in sorted({r['class'] for r in rows}):
        by_time = defaultdict(list)
        for r in rows:
            if r['class'] == cls:
                by_time[r['_t']].append(r)
        open_tracks = []
        for t in sorted(by_time):
            open_tracks = [tr for tr in open_tracks if t - tr[-1]['_t'] <= a.max_gap]
            free = list(open_tracks)
            # greedy: highest-confidence detections pick their nearest open track first
            for r in sorted(by_time[t], key=lambda r: -float(r['conf'])):
                best, bd = None, a.max_jump
                for tr in free:
                    last = tr[-1]
                    d = np.hypot(r['_cx'] - last['_cx'], r['_cy'] - last['_cy'])
                    if d <= bd:
                        best, bd = tr, d
                if best is None:
                    best = [r]; tracks.append(best); open_tracks.append(best)
                else:
                    best.append(r); free.remove(best)

    for i, tr in enumerate(tracks):
        ts = np.array([r['_t'] for r in tr]); ys = np.array([r['_cy'] for r in tr])
        drop = ys[-1] - ys[0]
        rate = np.polyfit(ts - ts[0], ys, 1)[0] if len(tr) >= 2 and ts[-1] > ts[0] else 0.0
        ok = (len(tr) >= a.min_hits and drop >= a.min_drop and rate >= a.min_rate and tr[-1]['_w'] >= tr[0]['_w'])
        for r in tr:
            r['track_id'], r['track_hits'], r['track_drop'], r['approaching'] = i, len(tr), round(float(drop), 3), int(ok)

    fields = [k for k in rows[0] if not k.startswith('_')]
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    n_ok = sum(1 for tr in tracks if tr[0]['approaching'])
    print(f'{len(rows)} detections in {len(tracks)} tracks; {n_ok} approaching tracks -> {out}')


if __name__ == '__main__':
    main()
