"""Build a local review pack: the checks an engineer does by eye before retraining.

  errors/  a short clip of every hazard the device logic missed and a sample of its false reports on the test videos,
           each with a cause to choose (error analysis)
  train/   the training images the last run actually saw (train_batch*.jpg), with a checklist
  labels/  a random 10 % of a labelled batch with its boxes drawn, for a second person to check

Everything stays local: the videos are YouTube footage for private use, so the pack is never committed or
published. Choices are saved in the browser and exported as CSV from each page.

  python scripts/review_pack.py --run runs/v6_batch6 --model v6last --batch youtube_batch6 --out runs/review_pack
"""
import argparse
import csv
import html
import json
import random
import shutil
import subprocess
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
VIDEOS = [  # name, video, events, detection folder prefix
    ('Night (Cairo, held out)', 'data/egypt_test/external/cairo_youtube_1.mp4', 'data/egypt_test/external/events_cairo.csv', 'cairo'),
    ('Day (Cairo, held out)', 'data/videos/cairo_yt_02_day.mp4', 'data/egypt_test/external/events_cairo_yt_02.csv', 'day'),
    ('Night (test 14)', 'data/videos/cairo_yt_14_night_test.mp4', 'data/tags/cairo_yt_14_night_test_events.csv', 'n14'),
    ('Day (test 15)', 'data/videos/cairo_yt_15_day_test.mp4', 'data/tags/cairo_yt_15_day_test_events.csv', 'd15'),
]
BEFORE, AFTER, GAP, LAG = 6.0, 2.0, 1.5, 3 / 60      # event_recall windows; detections stamped one stride early
COL = {'pothole': (255, 149, 0), 'speed_bump': (0, 170, 255)}
MISS_CAUSES = ['too small / far', 'too dark / glare', 'hidden (car, shadow)', 'model saw it, device logic rejected',
               'not visible at all (dip, flat)', 'tag at wrong time', 'wrong label (not a hazard)', 'other']
FALSE_CAUSES = ['kerb / road edge', 'shadow / marking / patch', 'flush manhole', 'vehicle / object',
                'real hazard, not tagged', 'broken surface or sunken manhole (counts now)', 'other']
FONT = '/System/Library/Fonts/HelveticaNeue.ttc'


def secs(mmss):
    m, s = mmss.split(':')
    return int(m) * 60 + float(s)


def rows(path):
    return list(csv.DictReader(open(path)))


def find_cases(run_prefix, model, events_csv):
    raw = rows(ROOT / f'runs/{run_prefix}_{model}/detections.csv')
    logic = [(float(r['t_s']) + LAG, r['class']) for r in rows(ROOT / f'runs/{run_prefix}_{model}/detections_device_logic.csv')]
    tags = [(secs(r['time']), r['type']) for r in rows(ROOT / events_csv) if r['type'] in COL]
    misses, false = [], []
    for t, c in tags:
        if not any(c == lc and t - BEFORE <= lt <= t + AFTER for lt, lc in logic):
            n_raw = sum(1 for r in raw if r['class'] == c and t - BEFORE <= float(r['t_s']) + LAG <= t + AFTER)
            misses.append({'t': t, 'type': c, 'raw_boxes': n_raw})
    stray = sorted((lt, lc) for lt, lc in logic
                   if not any(lc == c and t - BEFORE <= lt <= t + AFTER for t, c in tags))
    for c in COL:
        ts = [lt for lt, lc in stray if lc == c]
        group = []
        for lt in ts + [None]:
            if group and (lt is None or lt - group[-1] > GAP):
                false.append({'t': group[0], 'type': c, 'raw_boxes': len(group)})
                group = []
            if lt is not None:
                group.append(lt)
    return misses, sorted(false, key=lambda x: x['t']), raw


def render(video, raw, t_center, out, tags, kind, typ):
    """Clip from t-BEFORE to t+AFTER, 1280x720, real speed, model boxes drawn, the case marked."""
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    t0 = max(0.0, t_center - BEFORE)
    t1 = t_center + AFTER
    dets = {}
    for r in raw:
        t = float(r['t_s']) + LAG
        if t0 - 0.5 <= t <= t1:
            dets.setdefault(t, []).append(r)
    W, H = 1280, 720
    ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
                           '-r', '30', '-i', '-', '-c:v', 'libx264', '-crf', '24', '-pix_fmt', 'yuv420p',
                           '-movflags', '+faststart', str(out)], stdin=subprocess.PIPE)
    start = int(round(t0 * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    cur, frame = start - 1, None
    f_big, f_small = ImageFont.truetype(FONT, 24, index=1), ImageFont.truetype(FONT, 18, index=1)
    for k in range(int((t1 - t0) * 30)):
        want = start + int(round(k * fps / 30))
        while cur < want:
            ok, fr = cap.read()
            if not ok:
                break
            frame, cur = fr, cur + 1
        if frame is None:
            break
        t = want / fps
        img = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (W, H)), cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(img, 'RGBA')
        for x in [x for x in dets if 0 <= t - x <= 0.3]:
            for r in dets[x]:
                c = COL[r['class']]
                box = [float(r['x1']) * W, float(r['y1']) * H, float(r['x2']) * W, float(r['y2']) * H]
                d.rectangle(box, outline=c + (255,), width=3)
                d.text((box[0] + 3, box[1] - 22), f"{r['class'].replace('_', ' ')} {float(r['conf']):.2f}", font=f_small,
                       fill=c + (255,))
        label = (f"{'Missed' if kind == 'miss' else 'False report'}: {typ.replace('_', ' ')}  ·  "
                 f"{'tagged at' if kind == 'miss' else 'reported at'} {int(t_center // 60)}:{t_center % 60:04.1f}")
        d.rectangle([0, 0, W, 40], fill=(0, 0, 0, 170))
        d.text((14, 8), label, font=f_big, fill=(255, 255, 255))
        d.text((W - 110, 8), f'{int(t // 60)}:{t % 60:04.1f}', font=f_big, fill=(255, 255, 255))
        if abs(t - t_center) < 0.25:
            d.rectangle([0, H - 10, W, H], fill=(255, 255, 255, 230))
        ff.stdin.write(np.asarray(img).tobytes())
    ff.stdin.close()
    ff.wait()
    cap.release()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>TITLE</title><style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#ddddd6;--card:#fff;--acc:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecece8;--mut:#9b9b95;--line:#33332f;--card:#1f1f1d;--acc:#7aa7ec}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,"Helvetica Neue",Arial,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;
 gap:16px;align-items:center;flex-wrap:wrap;z-index:2}
h1{font-size:19px;margin:0}header p{margin:0;color:var(--mut);flex:1;min-width:260px}
button{font:inherit;padding:7px 14px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
main{padding:20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.card video,.card img{width:100%;display:block;background:#000}.meta{padding:10px 12px}
.meta b{display:block}.meta small{color:var(--mut)}select,input[type=text]{font:inherit;width:100%;margin-top:8px;padding:6px;
 border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);box-sizing:border-box}
label{display:block;margin-top:6px}.done{outline:2px solid var(--acc)}
@media (max-width:480px){main{grid-template-columns:1fr;padding:12px}}
</style></head><body><header><h1>TITLE</h1><p>INTRO</p><span id="count"></span>
<button onclick="exportCsv()">Export CSV</button></header><main id="m"></main>
<script>
const ITEMS = DATA, KEY = 'KEYNAME', FIELDS = FIELDLIST;
let saved = {}; try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {} count(); };
const count = () => { document.getElementById('count').textContent =
  ITEMS.filter(i => saved[i.id] && saved[i.id].choice).length + ' / ' + ITEMS.length + ' done'; };
const m = document.getElementById('m');
for (const it of ITEMS) {
  const c = document.createElement('div'); c.className = 'card';
  const media = it.video ? `<video src="${it.video}" controls loop muted playsinline preload="metadata"></video>`
                         : `<img src="${it.image}" loading="lazy" alt="">`;
  const opts = it.choices.map(o => `<option>${o}</option>`).join('');
  const checks = (it.checks || []).map((q, k) => `<label><input type="checkbox" data-k="${k}"> ${q}</label>`).join('');
  c.innerHTML = `${media}<div class="meta"><b>${it.title}</b><small>${it.sub}</small>
    <select><option value="">choose…</option>${opts}</select>${checks}<input type="text" placeholder="note (optional)"></div>`;
  const s = saved[it.id] || {}; const sel = c.querySelector('select'), note = c.querySelector('input[type=text]');
  sel.value = s.choice || ''; note.value = s.note || ''; if (s.choice) c.classList.add('done');
  c.querySelectorAll('input[type=checkbox]').forEach(b => { b.checked = !!(s.checks || {})[b.dataset.k];
    b.onchange = () => { saved[it.id] = saved[it.id] || {}; (saved[it.id].checks = saved[it.id].checks || {})[b.dataset.k] = b.checked; save(); }; });
  sel.onchange = () => { saved[it.id] = {...(saved[it.id] || {}), choice: sel.value}; c.classList.toggle('done', !!sel.value); save(); };
  note.oninput = () => { saved[it.id] = {...(saved[it.id] || {}), note: note.value}; save(); };
  m.appendChild(c);
}
count();
function exportCsv() {
  const head = [...FIELDS, 'choice', 'checks', 'note'];
  const esc = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const lines = [head.join(',')].concat(ITEMS.map(it => { const s = saved[it.id] || {};
    const ch = Object.entries(s.checks || {}).filter(([, v]) => v).map(([k]) => it.checks[k]).join('; ');
    return [...FIELDS.map(f => it[f]), s.choice, ch, s.note].map(esc).join(','); }));
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\\n')], {type: 'text/csv'}));
  a.download = KEY + '.csv'; a.click();
}
</script></body></html>"""


def page(path, title, intro, key, items, fields):
    path.write_text(PAGE.replace('TITLE', html.escape(title)).replace('INTRO', html.escape(intro))
                    .replace('KEYNAME', key).replace('FIELDLIST', json.dumps(fields)).replace('DATA', json.dumps(items)))


def errors(out, model, max_false):
    (out / 'errors').mkdir(parents=True, exist_ok=True)
    items, rng = [], random.Random(0)
    for name, video, events, prefix in VIDEOS:
        misses, false, raw = find_cases(prefix, model, events)
        false = sorted(rng.sample(false, min(len(false), max_false)), key=lambda x: x['t'])
        for kind, cases in (('miss', misses), ('false', false)):
            for c in cases:
                cid = f"{prefix}_{kind}_{c['type']}_{c['t']:.1f}"
                render(ROOT / video, raw, c['t'], out / 'errors' / f'{cid}.mp4', None, kind, c['type'])
                items.append({'id': cid, 'video': f'{cid}.mp4', 'kind': kind, 'type': c['type'], 'time_s': round(c['t'], 1),
                              'source': name, 'raw_boxes': c['raw_boxes'],
                              'title': f"{'Missed' if kind == 'miss' else 'False report'}: {c['type'].replace('_', ' ')}",
                              'sub': f"{name}, {int(c['t'] // 60)}:{c['t'] % 60:04.1f}"
                                     + (f" · model boxes near it: {c['raw_boxes']}" if kind == 'miss' else ''),
                              'choices': MISS_CAUSES if kind == 'miss' else FALSE_CAUSES})
        print(f'{name}: {len(misses)} misses, {len(false)} false reports (sampled)', flush=True)
    page(out / 'errors' / 'index.html', 'Error review',
         f'Every hazard {model} with the device logic missed on the test videos, and a sample of its false reports. '
         'Play each clip (the white bar marks the tagged or reported moment) and choose the cause.',
         f'errors_{model}', items, ['id', 'kind', 'type', 'time_s', 'source', 'raw_boxes'])


def train_images(out, run):
    d = out / 'train'
    d.mkdir(parents=True, exist_ok=True)
    checks = ['every visible hazard has a box', 'boxes fit tightly', 'no box on something you cannot see',
              'looks like a real road from a vehicle (no stock or close-up photo)']
    items = []
    for p in sorted(Path(run).glob('train_batch*.jpg')):
        shutil.copy(p, d / p.name)
        items.append({'id': p.stem, 'image': p.name, 'title': p.name, 'file': p.name,
                      'sub': 'early epochs (mosaic)' if p.stem in ('train_batch0', 'train_batch1', 'train_batch2')
                             else 'last epochs (no mosaic)',
                      'choices': ['looks fine', 'some problems', 'many problems'], 'checks': checks})
    page(d / 'index.html', 'Training images check',
         f'What {Path(run).name} trained on (16 images per picture; 0 = pothole, 1 = speed bump). Zoom in, tick what holds, '
         'and note the file names of bad tiles.', f'train_{Path(run).name}', items, ['file'])


def label_sample(out, batch, share):
    d = out / 'labels'
    d.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1)
    names = {0: 'speed_bump', 1: 'pothole'}
    items = []
    for zname, kind in ((batch, 'boxed'), ('background_' + batch.split('_', 1)[1], 'empty')):
        with zipfile.ZipFile(ROOT / 'data/reviewed' / f'{zname}.zip') as z:
            jpgs = sorted(n for n in z.namelist() if n.endswith('.jpg'))
            for n in sorted(rng.sample(jpgs, max(1, round(share * len(jpgs))))):
                img = Image.open(z.open(n)).convert('RGB')
                lab = n.replace('images/', 'labels/').rsplit('.', 1)[0] + '.txt'
                dr = ImageDraw.Draw(img)
                if lab in z.namelist():
                    for ln in z.read(lab).decode().split('\n'):
                        f = ln.split()
                        if len(f) == 5:
                            c, x, y, w, h = int(f[0]), *map(float, f[1:])
                            W, H = img.size
                            col = COL[names.get(c, 'pothole')]
                            dr.rectangle([(x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H], outline=col, width=4)
                img.thumbnail((1280, 1280))
                fn = f'{Path(n).stem}.jpg'
                img.save(d / fn, quality=85)
                items.append({'id': Path(n).stem, 'image': fn, 'frame': Path(n).stem, 'kind': kind,
                              'title': Path(n).stem, 'sub': 'has boxes (orange pothole, blue bump)' if kind == 'boxed'
                                                            else 'left empty: should show no hazard',
                              'choices': ['correct', 'a box is wrong', 'a hazard is missing a box', 'should be unsure']})
    page(d / 'index.html', 'Label check',
         f'A random {share:.0%} of {batch}, checked by a second person against LABELLING_POLICY.md (including the new '
         'rule: sunken / raised / open manholes and broken surface are potholes).', f'labels_{batch}', items, ['frame', 'kind'])
    print(f'label check: {len(items)} frames', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default=ROOT / 'runs/v6_batch6', type=Path)
    ap.add_argument('--model', default='v6last', help='suffix of the runs/<video>_<model> detection folders')
    ap.add_argument('--batch', default='youtube_batch6')
    ap.add_argument('--share', type=float, default=0.10)
    ap.add_argument('--max-false', type=int, default=12, help='false reports sampled per video')
    ap.add_argument('--out', default=ROOT / 'runs/review_pack', type=Path)
    ap.add_argument('--only', nargs='*', choices=['errors', 'train', 'labels'])
    a = ap.parse_args()
    parts = a.only or ['errors', 'train', 'labels']
    if 'train' in parts:
        train_images(a.out, a.run)
    if 'labels' in parts:
        label_sample(a.out, a.batch, a.share)
    if 'errors' in parts:
        errors(a.out, a.model, a.max_false)
    print('open', a.out / '<part>' / 'index.html')


if __name__ == '__main__':
    main()
