"""Support for Espressif's ESPDet-Pico detector (github.com/espressif/esp-detection, AGPL-3.0).

ESPDet-Pico is a 0.36 M-parameter YOLO11-based model that runs on an ESP32-S3 (about 126 ms per
frame at 224 x 224). It is trained with normal Ultralytics, plus Espressif's own layer definitions.
fetch() downloads those few files, pinned to one commit, into third_party/esp-detection/ (not in
git); enable() makes Ultralytics able to build and load ESPDet models. enable() does nothing if the
files are not there, so importing this module is harmless for the YOLO11n runs.

Usage:  python scripts/espdet_support.py        # download the files once
"""
import sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'third_party/esp-detection'
COMMIT = '28de5902a357d99dd9a5db0f52905255641c5a78'
FILES = ['LICENSE', 'cfg/models/espdet_pico.yaml', 'nn/esp_tasks.py', 'nn/modules/__init__.py',
         'nn/modules/esp_block.py', 'nn/modules/esp_conv.py', 'nn/modules/esp_head.py']
MODEL_YAML = DEST / 'cfg/models/espdet_pico.yaml'


def fetch():
    for f in FILES:
        out = DEST / f
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f'https://raw.githubusercontent.com/espressif/esp-detection/{COMMIT}/{f}', out)
    print(f'ESPDet files ready in {DEST} (commit {COMMIT[:7]})')


def enable():
    if not (DEST / 'nn/esp_tasks.py').exists():
        return False
    if str(DEST) not in sys.path:
        sys.path.insert(0, str(DEST))
    import ultralytics.nn.tasks as tasks
    from nn.esp_tasks import custom_parse_model
    tasks.parse_model = custom_parse_model
    return True


if __name__ == '__main__':
    fetch()
    print('enabled:', enable())
