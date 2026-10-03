"""Quantise an ESPDet-Pico model to 8 bits for the ESP32-S3 with Espressif's ESP-PPQ, and measure the
accuracy lost, without any hardware.

On the chip the model runs in 8-bit integers, which usually costs some accuracy. This script follows
Espressif's own pipeline (esp-detection/deploy): export to ONNX, quantise with ESP-PPQ using calibration
images, then run the quantised graph in ESP-PPQ's simulator on the test split and compare it with the
full-precision model on the same images. It also writes the .espdl file that would be flashed to the chip.

Needs ESP-PPQ, which runs on Python 3.8-3.11 (see third_party/venv-ppq), and the Espressif files:
    python scripts/espdet_support.py
    third_party/venv-ppq/bin/python scripts/espdet_quantize.py --weights models/espdet_pico_224.pt --imgsz 224

Writes results/espdet_quantization.json and models/<name>_<target>.espdl.

With --samples N it also exports N test images, letterboxed to the model input, as raw RGB888 files for the
on-chip test (../firmware/espdet_qemu), with the detections of the float and the simulated 8-bit model on
each as the reference the chip's output is compared with.
"""
import argparse, json, random, shutil, sys, zipfile
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import espdet_support

ROOT = Path(__file__).resolve().parents[1]


def calibration_images(data_yaml: Path, out: Path, n: int, seed=0):
    """A mix of training images: public data plus Egyptian day and night frames."""
    import yaml
    d = yaml.safe_load(data_yaml.read_text())
    base = Path(d.get('path', data_yaml.parent))
    imgs = sorted((base / d['train']).glob('*.jpg'))
    random.Random(seed).shuffle(imgs)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for p in imgs[: n * 3 // 4]:
        shutil.copy(p, out / p.name)
    # Egyptian frames, if the reviewed zips are on this machine (they are not in git)
    eg = sorted((ROOT / 'data/reviewed').glob('youtube_batch*.zip'))
    names = [(z, m) for z in eg for m in zipfile.ZipFile(z).namelist() if m.endswith('.jpg')]
    random.Random(seed).shuffle(names)
    for z, m in names[: n - len(list(out.iterdir()))]:
        (out / Path(m).name).write_bytes(zipfile.ZipFile(z).read(m))
    return len(list(out.iterdir()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', default=ROOT / 'models/espdet_pico_224.pt', type=Path)
    ap.add_argument('--imgsz', type=int, default=224)
    ap.add_argument('--data', default=ROOT / 'data/merged/data.yaml', type=Path)
    ap.add_argument('--split', default='test')
    ap.add_argument('--target', default='esp32s3', choices=['esp32s3', 'esp32p4'])
    ap.add_argument('--calib', type=int, default=256, help='number of calibration images')
    ap.add_argument('--samples', type=int, default=0, help='export N test images + reference detections')
    ap.add_argument('--samples-out', type=Path, default=ROOT.parent / 'firmware/espdet_qemu/main/samples')
    ap.add_argument('--skip-val', action='store_true', help='skip the test-set scoring (step 3)')
    ap.add_argument('--error-report', action='store_true',
                    help="ESP-PPQ's per-layer error report (slow: about 10 minutes on a laptop CPU)")
    a = ap.parse_args()

    assert espdet_support.enable(), 'run: python scripts/espdet_support.py'
    sys.path.insert(0, str(espdet_support.DEST))
    from deploy.export import Export
    import deploy.quantize as dq
    from esp_ppq.executor import TorchExecutor
    from ultralytics import YOLO
    from ultralytics.nn.modules.head import Detect

    work = ROOT / 'runs' / f'quant_{a.weights.stem}'
    work.mkdir(parents=True, exist_ok=True)
    pt = work / 'best.pt'
    shutil.copy(a.weights, pt)
    names = YOLO(str(pt)).names
    nc = len(names)

    # 1. ONNX export with Espressif's exporter (six raw head outputs, no decoding on the chip)
    onnx_path = pt.with_suffix('.onnx')
    onnx_path.unlink(missing_ok=True)
    try:
        Export(str(pt), a.imgsz)
    except TypeError:
        # Espressif's exporter returns (file, model); newer Ultralytics expects only the file and fails
        # while printing the summary, after the ONNX file has been written
        pass
    assert onnx_path.exists(), 'ONNX export failed'

    # 2. 8-bit quantisation, calibrated on training images
    calib = work / 'calib'
    n_cal = calibration_images(a.data, calib, a.calib)
    espdl = ROOT / 'models' / f'{a.weights.stem}_{a.target}.espdl'
    if not a.error_report:
        orig = dq.espdl_quantize_onnx
        dq.espdl_quantize_onnx = lambda *args, **kw: orig(*args, **{**kw, 'error_report': False})
    graph = dq.quant_espdet(onnx_path=str(onnx_path), target=a.target, num_of_bits=8, device='cpu',
                         batchsz=32, imgsz=a.imgsz, calib_dir=str(calib), espdl_model_path=str(espdl))
    executor = TorchExecutor(graph=graph, device='cpu')

    # 3. Score the simulated 8-bit graph with the standard validator: the model's forward pass is replaced
    # by the quantised graph (Espressif's own validator targets an older Ultralytics). Square inputs for both,
    # because the graph was exported at a fixed size.
    head = Detect(nc=nc, reg_max=1, end2end=False, ch=[32, 64, 128])
    head.stride = torch.tensor([8.0, 16.0, 32.0])

    def int8_forward(x, *args, **kwargs):
        out = executor(x.float())
        bs = x.shape[0]
        boxes = torch.cat([out[2 * i].view(bs, 4, -1) for i in range(3)], dim=-1)
        scores = torch.cat([out[2 * i + 1].view(bs, nc, -1) for i in range(3)], dim=-1)
        return head._inference(dict(boxes=boxes, scores=scores, feats=[out[i] for i in range(0, 6, 2)]))

    if a.samples:
        export_samples(a, pt, int8_forward, a.samples, a.samples_out)
    if a.skip_val:
        return

    common = dict(data=str(a.data), split=a.split, imgsz=a.imgsz, device='cpu', batch=1, rect=False,
                  plots=False, verbose=False)
    fp = YOLO(str(pt)).val(project=str(work), name='val_float', exist_ok=True, **common)
    qm = YOLO(str(pt))
    qm.model.fuse = lambda *a, **k: qm.model  # keep the module as is; only forward() matters
    qm.model.forward = int8_forward
    q8 = qm.val(project=str(work), name='val_int8', exist_ok=True, **common)

    def summary(r):
        return {'mAP50': round(float(r.box.map50), 3),
                **{names[i]: round(float(r.box.ap50[k]), 3) for k, i in enumerate(r.box.ap_class_index)}}
    res = {'weights': a.weights.name, 'imgsz': a.imgsz, 'target': a.target, 'split': a.split,
           'calibration_images': n_cal, 'float32': summary(fp), 'int8_simulated': summary(q8),
           'espdl_file': espdl.name, 'espdl_kb': round(espdl.stat().st_size / 1024, 1)}
    (ROOT / 'results/espdet_quantization.json').write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


def export_samples(a, pt, int8_forward, n, out: Path, score_thr=0.25, nms_thr=0.7, seed=0):
    """Test images (half with a speed bump, half with a pothole, objects large enough to see at the model's
    input size), letterboxed exactly as the chip will receive them, plus the float and 8-bit detections."""
    import cv2, yaml
    import numpy as np
    from ultralytics import YOLO
    from ultralytics.data.augment import LetterBox
    from ultralytics.utils.nms import non_max_suppression

    d = yaml.safe_load(a.data.read_text())
    img_dir = Path(d.get('path', a.data.parent)) / d[a.split]
    if not img_dir.is_absolute():
        img_dir = ROOT / img_dir
    imgs = sorted(img_dir.glob('*.jpg'))
    random.Random(seed).shuffle(imgs)
    picked = {0: [], 1: []}
    for im in imgs:
        lab = Path(str(im).replace('/images/', '/labels/')).with_suffix('.txt')
        rows = [list(map(float, l.split())) for l in lab.read_text().splitlines() if l.strip()] if lab.exists() else []
        big = [r for r in rows if r[3] * r[4] >= 0.02]
        cls = 1 if any(r[0] == 1 for r in big) else 0 if big and all(r[0] == 0 for r in rows) else None
        if cls is not None and len(picked[cls]) < (n + 1 - cls) // 2:
            picked[cls].append((im, rows))
        if sum(map(len, picked.values())) >= n:
            break

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    fmodel = YOLO(str(pt)).model.float().eval()
    lb = LetterBox(new_shape=(a.imgsz, a.imgsz), auto=False, scaleup=True)

    def dets(pred):
        pred = pred[0] if isinstance(pred, (tuple, list)) else pred
        r = non_max_suppression(pred, conf_thres=score_thr, iou_thres=nms_thr, max_det=10)[0]
        return [{'cls': int(c), 'score': round(float(s), 3), 'box': [round(float(v), 1) for v in b]}
                for *b, s, c in r.tolist()]

    ref = []
    for i, (im, rows) in enumerate(picked[1] + picked[0]):
        bgr = cv2.imread(str(im))
        h0, w0 = bgr.shape[:2]
        boxed = lb(image=bgr)
        rgb = np.ascontiguousarray(boxed[:, :, ::-1])
        name = f'sample{i}'
        (out / f'{name}.rgb').write_bytes(rgb.tobytes())
        cv2.imwrite(str(out / f'{name}.jpg'), boxed)
        # ground truth in letterboxed pixels
        r = min(a.imgsz / h0, a.imgsz / w0)
        pw, ph = (a.imgsz - round(w0 * r)) / 2, (a.imgsz - round(h0 * r)) / 2
        gt = [{'cls': int(c), 'box': [round((x - w / 2) * w0 * r + pw, 1), round((y - h / 2) * h0 * r + ph, 1),
                                       round((x + w / 2) * w0 * r + pw, 1), round((y + h / 2) * h0 * r + ph, 1)]}
              for c, x, y, w, h in rows]
        x = torch.from_numpy(rgb).permute(2, 0, 1)[None].float() / 255
        with torch.no_grad():
            ref.append({'name': name, 'source': im.name, 'ground_truth': gt,
                        'float32': dets(fmodel(x)), 'int8_simulated': dets(int8_forward(x))})
    (out / 'reference.json').write_text(json.dumps({'imgsz': a.imgsz, 'score_thr': score_thr, 'nms_thr': nms_thr,
                                                    'classes': YOLO(str(pt)).names, 'samples': ref}, indent=1))
    print(f'{len(ref)} samples written to {out}')


if __name__ == '__main__':
    main()
