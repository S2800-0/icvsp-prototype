"""Download the public SBP-YOLO dataset without signing in to Google.

The authors share it as a public Google Drive folder of loose files (about 14,750 images and
labels), which is too many for `gdown --folder` (50-file limit). This script lists the folder
through Drive's public embedded view and downloads each file in parallel, with retries.
Files that already exist are skipped, so it is safe to re-run after an interruption.

Dataset: Liang et al., "SBP-YOLO", J. Real-Time Image Processing 23, 52 (2026).
         https://github.com/chuanqi1997/SBP-YOLO

Usage:  python scripts/download_sbp.py [--out /content/raw/sbp_yolo] [--workers 16]
"""
import argparse, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

DATASET_FOLDER = '1anMQJlIeVCxH2xsk-cGJwVRb166ztyyJ'  # SBP-YOLO-Dataset/dataset (train/valid/test)
ENTRY = re.compile(r'<a href="https://drive.google.com/(drive/folders|file/d)/([^/"?]+)[^"]*"[^>]*>.*?'
                   r'flip-entry-title">([^<]+)', re.S)
session = requests.Session()


def list_folder(fid, prefix=''):
    html = session.get(f'https://drive.google.com/embeddedfolderview?id={fid}', timeout=60).text
    files = []
    for kind, item_id, name in ENTRY.findall(html):
        if kind == 'drive/folders':
            files += list_folder(item_id, f'{prefix}{name}/')
        else:
            files.append((f'{prefix}{name}', item_id))
    return files


def fetch(rel, fid, out: Path, tries=6):
    dst = out / rel
    if dst.exists() and dst.stat().st_size > 0:
        return 'skipped'
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(tries):
        try:
            r = session.get('https://drive.usercontent.google.com/download',
                            params={'id': fid, 'export': 'download'}, timeout=60)
            if r.status_code == 200 and not r.headers.get('content-type', '').startswith('text/html'):
                dst.write_bytes(r.content)
                return 'ok'
        except requests.RequestException:
            pass
        time.sleep(2 ** attempt)  # back off on rate limits (429) and transient errors
    return 'failed'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='/content/raw/sbp_yolo', type=Path)
    ap.add_argument('--workers', type=int, default=16)
    a = ap.parse_args()

    print('Listing the public folder ...')
    files = list_folder(DATASET_FOLDER)
    print(f'{len(files)} files found; downloading with {a.workers} workers')
    stats, failed = {'ok': 0, 'skipped': 0, 'failed': 0}, []
    with ThreadPoolExecutor(a.workers) as pool:
        jobs = {pool.submit(fetch, rel, fid, a.out): rel for rel, fid in files}
        for n, job in enumerate(as_completed(jobs), 1):
            res = job.result()
            stats[res] += 1
            if res == 'failed':
                failed.append(jobs[job])
            if n % 1000 == 0 or n == len(files):
                print(f'  {n}/{len(files)}  {stats}')
    if failed:
        (a.out / 'failed.txt').write_text('\n'.join(failed))
        print(f'{len(failed)} files failed (listed in failed.txt). Re-run this script to retry them.')
    else:
        print('All files downloaded.')


if __name__ == '__main__':
    main()
