"""Turn the team's short phone clips into frames for labelling.

Put clips in data/egypt_test/clips/<area>_<person>_<n>.mov (the area prefix is used to
split by location, never by frame). Frames go to data/egypt_test/frames/<clip-name>/.

Usage:  python scripts/extract_frames.py [--fps 2]
"""
import argparse, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fps', type=float, default=2)
    a = ap.parse_args()
    clips = sorted(p for p in (ROOT / 'data/egypt_test/clips').iterdir()
                   if p.suffix.lower() in {'.mov', '.mp4', '.m4v'})
    for clip in clips:
        out = ROOT / 'data/egypt_test/frames' / clip.stem
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', str(clip), '-vf', f'fps={a.fps}',
                        '-q:v', '2', str(out / f'{clip.stem}_%04d.jpg')], check=True)
        print(f'{clip.name}: {len(list(out.glob("*.jpg")))} frames')


if __name__ == '__main__':
    main()
