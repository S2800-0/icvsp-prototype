"""Score the detector against the team's list of bumps and potholes in a clip (event level).

An event counts as DETECTED if the model reported the same class at least --min-hits times in the
window [event - before, event + after] seconds: the car approaches the hazard before reaching it.
Detections outside every window of their class are grouped into episodes (gaps > --gap s split
them) and listed with their time. Each one is either a false alarm or a real hazard the list
missed, so check them in the annotated video.

Events of other types (e.g. "dip") are reported but not scored.

Usage:  python scripts/event_recall.py runs/egypt_clips_v1/detections.csv data/egypt_test/events.csv
"""
import argparse, csv
from collections import defaultdict

SCORED = ('speed_bump', 'pothole')


def secs(mmss: str) -> float:
    m, s = mmss.replace(' ', '').split(':')
    return int(m) * 60 + float(s)


def fmt(t: float) -> str:
    return f'{int(t // 60)}:{t % 60:04.1f}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('detections')
    ap.add_argument('events')
    ap.add_argument('--before', type=float, default=6.0)
    ap.add_argument('--after', type=float, default=2.0)
    ap.add_argument('--min-hits', type=int, default=2)
    ap.add_argument('--gap', type=float, default=1.5)
    ap.add_argument('--out', default=None, help='optional CSV with one row per event')
    a = ap.parse_args()

    dets = defaultdict(list)                       # (clip, class) -> [(t, conf)]
    for r in csv.DictReader(open(a.detections)):
        dets[(r['clip'], r['class'])].append((float(r['t_s']), float(r['conf'])))
    events = [dict(r, t=secs(r['time'])) for r in csv.DictReader(open(a.events))]
    events.sort(key=lambda e: (e['clip'], e['t']))

    rows, used = [], defaultdict(set)
    print(f"{'clip':26s} {'time':>6s}  {'type':11s} {'result':9s} hits  max conf")
    for e in events:
        if e['type'] not in SCORED:
            print(f"{e['clip']:26s} {e['time']:>6s}  {e['type']:11s} {'not scored':9s}")
            continue
        lo, hi = e['t'] - a.before, e['t'] + a.after
        inside = [(i, t, c) for i, (t, c) in enumerate(dets[(e['clip'], e['type'])]) if lo <= t <= hi]
        used[(e['clip'], e['type'])].update(i for i, _, _ in inside)
        ok = len(inside) >= a.min_hits
        best = max((c for _, _, c in inside), default=0)
        first = min((t for _, t, _ in inside), default=None)
        lead = f'  first seen {e["t"] - first:.1f} s before' if ok else ''
        print(f"{e['clip']:26s} {e['time']:>6s}  {e['type']:11s} {'DETECTED' if ok else 'missed':9s} {len(inside):4d}  {best:.2f}{lead}")
        rows.append({'clip': e['clip'], 'time': e['time'], 'type': e['type'], 'detected': ok,
                     'hits': len(inside), 'max_conf': round(best, 3)})

    print('\nEvent recall (the hazard was flagged while approaching it):')
    for cls in SCORED:
        ev = [r for r in rows if r['type'] == cls]
        if ev:
            k = sum(r['detected'] for r in ev)
            print(f'  {cls:11s} {k}/{len(ev)} = {k / len(ev):.0%}')

    print(f'\nDetections outside every listed event (≥ {a.min_hits} hits): check these times in the annotated video')
    total = 0
    for (clip, cls), lst in sorted(dets.items()):
        free = sorted((t, c) for i, (t, c) in enumerate(lst) if i not in used[(clip, cls)])
        episodes, cur = [], []
        for t, c in free:
            if cur and t - cur[-1][0] > a.gap:
                episodes.append(cur); cur = []
            cur.append((t, c))
        if cur:
            episodes.append(cur)
        episodes = [ep for ep in episodes if len(ep) >= a.min_hits]
        total += len(episodes)
        if episodes:
            print(f'  {clip} · {cls}: ' + ', '.join(f"{fmt(ep[0][0])} ({len(ep)} hits, max {max(c for _, c in ep):.2f})"
                                                   for ep in episodes))
    print(f'  {total} unlisted episodes in total')

    if a.out:
        with open(a.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)


if __name__ == '__main__':
    main()
