# ICVSP: Trust-Aware Cooperative Vehicle Safety Platform (prototype)

Graduation project, 2026–2027. A low-cost, retrofit in-vehicle unit that detects road hazards
(potholes, speed bumps), verifies them with on-board sensors and reports from other vehicles, and
warns approaching drivers. It keeps working when the cloud or network is unavailable.

This repository holds the working prototypes:
- **`engine/`: the trust & consensus engine**, the decision layer and the core of the project. It decides, with a
  written reason, whether reported hazards are real enough to warn anyone, and who.
- **`ai/`: the camera-based hazard detectors** that produce the reports: YOLO11n for the Pro unit and ESPDet-Pico
  for a low-cost ESP32-S3 unit.
- **`firmware/`: the device firmware**: IMU hazard detection, signed reports and replay protection, unit-tested on a
  laptop, and the ESPDet-Pico model built for the ESP32-S3.

## Current status

| Component | Status |
|---|---|
| Trust & consensus engine (schema checks, plausibility, trust, consensus, Sybil / replay defence, targeting) | ✅ Prototype, 41 tests, evaluated in simulation; accepts pothole and speed-bump reports |
| Camera detector, Pro unit (YOLO11n) | ✅ v3c, fine-tuned on Egyptian day and night footage: public test mAP50 0.846 |
| False-alarm filters for speed bumps (road-surface check, motion check) | ✅ Cut false bump flags by over 95 % on held-out Egyptian video |
| Egyptian road tests | 🟡 Potholes detected above chance by day and night; many unpainted bumps are barely visible on camera, so the IMU becomes the main bump sensor. Own recordings needed for a larger test set |
| Device firmware (IMU detector, signed reports, replay window) | ✅ 30 unit tests on a laptop (PlatformIO native); thresholds still to be tuned on real recordings |
| Camera detector, Lite unit (ESPDet-Pico on ESP32-S3) | 🟡 8-bit model 486 KB, speed-bump mAP50 0.72 in ESP-PPQ simulation; builds and runs in the ESP32-S3 emulator, on-chip accuracy needs a board |
| V2V radio, cloud, in-vehicle hardware | ⏳ Planned (see roadmap) |

## Engine: simulated results

2,000 simulated runs per setting against two baselines (B1: warn on any report; B2: majority vote).
Detector assumptions: 85 % hit rate, 5 % false-alarm rate. Weights and thresholds are untuned.

| Attackers (share of vehicles) | B1 false alerts | B2 false alerts | **Engine false alerts** | Engine accuracy |
|---|---|---|---|---|
| 20 %, new identities | 46.9 % | 24.3 % | **0.5 %** | 96.0 % |
| 30 %, new identities | 49.2 % | 41.6 % | **1.0 %** | 94.0 % |
| 30 %, established "insider" identities | 49.2 % | 41.6 % | **15.2 %** ✗ (target ≤ 10 %) | 76.5 % |

**Honest limits:** the engine fails its target against insiders with a good history (the open research problem we
are working on), and packet loss raises missed hazards. These are simulated results, not real traffic. Full tables,
charts and method: [`engine/results/summary.md`](engine/results/summary.md) and [`engine/README.md`](engine/README.md).

Run it (plain Python 3.10+, no dependencies): `cd engine && python3 -m icvsp.demo all` for the seven scenarios
(single report, duplicates, false report, Sybil, cloud outage, replay, GPS spoofing), and `python3 -m unittest` for the tests.

## Detector: results on a public test set (1,104 unseen images)

YOLO11n trained on the public [SBP-YOLO dataset](https://github.com/chuanqi1997/SBP-YOLO) after near-duplicate removal.
Best accuracy: `v2_e150_960` (150 epochs, 960 px). `v1_e150` (640 px) is about 2.25× cheaper per frame; the in-vehicle speed benchmark decides which one runs on the unit. `v0_public_merge` (50 epochs) is the baseline.

| Class | v0 P / R (mAP50) | v1 P / R (mAP50) | **v2 P / R (mAP50)** |
|---|---|---|---|
| Speed bump | 0.89 / 0.86 (0.90) | 0.92 / 0.89 (0.93) | **0.90 / 0.90 (0.93)** |
| Pothole | 0.82 / 0.60 (0.69) | 0.83 / 0.65 (0.72) | **0.82 / 0.68 (0.75)** |
| **Overall mAP50** | 0.795 | 0.825 (+0.030) | **0.837** (+0.042) |

<p align="center"><img src="ai/results/v2_e150_960/test_confusion_matrix_normalized.png" width="520" alt="Normalised confusion matrix of v2 on the public test set"></p>

**What we learned**
- **Speed bumps** exceed our pre-set acceptance threshold (recall ≥ 0.75, precision ≥ 0.70) on public data; only 6 % are
  missed. Whether that holds on **Egyptian** roads is the open question; the Egyptian test set decides it.
- **Potholes** remain harder: 27 % are missed (mostly small, distant ones) and most false alarms are pothole-like patches or
  shadows. The two classes are never confused with each other. This supports confirming camera detections with the IMU and
  with reports from other vehicles rather than trusting the camera alone.
- **Longer training has reached its limit:** v1's best epoch was 139 of 150 and validation accuracy was nearly flat after
  epoch 100.
- **Larger images help small potholes, at a cost:** v2 (960 px) raises pothole recall from 0.65 to 0.68 and mAP50 from
  0.72 to 0.75, but needs about 2.25× the computation per frame. Speed bumps barely change.
- **Duplicates:** 676 near-duplicate images (9 %) were removed before training, including training images that duplicated
  test images, so our test numbers are not inflated by leakage.
- **Learning curve** (v0 setup): validation mAP50 0.695 → 0.744 → 0.764 → 0.784 at 25/50/75/100 % of the data.

**Since then** (details in [`ai/results/`](ai/results/)):
- **Egyptian fine-tuning (v3–v3c):** three labelling rounds on day and night Cairo footage, used privately for
  experiments only. v3c, with kerbs added as hard negatives, has the best public-test result so far (mAP50 0.846).
- **Speed-bump filters:** a bump box is kept only if it lies on the road surface (SegFormer road segmentation) and
  moves towards the car like a fixed object across frames. Together they remove over 95 % of false bump flags while
  keeping about 4 in 5 of the real bumps the model finds ([`road_filter.md`](ai/results/road_filter.md),
  [`motion_check.md`](ai/results/motion_check.md)).
- **Less computation:** processing only the bottom 60 % of the frame cuts the detector from 8.3 to 5.4 GFLOPs at 960 px
  (2.3 at 640 px) with no measurable loss ([`crop_test.md`](ai/results/crop_test.md)).
- **ESPDet-Pico for the ESP32-S3:** Espressif's 0.36 M-parameter model, trained on the same data. Weak on potholes
  (mAP50 0.27) but usable for speed bumps (0.78; 0.72 after 8-bit quantisation with ESP-PPQ), so we propose it as a
  speed-bump camera for a low-cost Lite unit ([`espdet_pico.md`](ai/results/espdet_pico.md)).

Models trained on private video frames (v3–v3c, ESPDet-Pico) are kept local and are not in this repository.
The full experiment log, updated after every run, is in [`ai/results/experiments.md`](ai/results/experiments.md).

## Device firmware

The device logic of the ESP32-S3 unit is plain C++ with no hardware calls, so it is built and tested on a laptop with
PlatformIO native unit testing, with simulated IMU and GNSS:
- **IMU hazard detector** (proposed FR-27): removes gravity, adapts its threshold to the road's vibration, and tells
  potholes (wheel drops first) from speed bumps (lifts first, for longer).
- **Safety events** in exactly the engine's format, **Ed25519 signatures** (RFC 8032) over sender, message ID, time and
  event, and a **30-second replay window** on GNSS time.
- An end-to-end check verifies the firmware's signed reports with the engine's validation and an independent Ed25519
  library.

`firmware/espdet_qemu` runs the 8-bit ESPDet-Pico model with Espressif's ESP-DL on the ESP32-S3. In Espressif's QEMU
emulator it builds, loads and runs, but the emulator gives wrong detections even for Espressif's own reference model,
so on-chip accuracy and speed will be measured on a board. Details: [`firmware/README.md`](firmware/README.md) and
[`firmware/espdet_qemu/README.md`](firmware/espdet_qemu/README.md).

```bash
cd firmware && pio test -e native          # 30 unit tests
python tools/check_with_engine.py          # signed reports checked from the backend side
```

## Try it

- **Reproduce training:** [`ai/ICVSP_train_kaggle.ipynb`](ai/ICVSP_train_kaggle.ipynb) runs unattended on Kaggle (recommended for
  long runs); [`ai/ICVSP_train.ipynb`](ai/ICVSP_train.ipynb) is the interactive Google Colab version. Neither needs Google Drive
  access; both download the public dataset themselves.
- **Use the trained models:** [`ai/models/v2_e150_960.pt`](ai/models/v2_e150_960.pt) (most accurate; run with `--imgsz 960`) or
  [`ai/models/v1_e150.pt`](ai/models/v1_e150.pt) (faster), for example
  `python ai/scripts/predict_clips.py --weights ai/models/v1_e150.pt` on your own dash-cam clips.

Details, including how to record and label the Egyptian test set, are in [`ai/README.md`](ai/README.md).

## Repository layout

```
engine/
├── icvsp/              decision engine: validation, trust, consensus, risk, simulator, experiments
├── tests/              41 tests, including every scenario's expected outcome and cold start
├── schema/             Safety Event JSON schema
└── results/            Stage 2 experiment results and charts
ai/
├── ICVSP_train.ipynb   Colab notebook: download → merge → train → evaluate
├── scripts/            dataset download and merge, training, evaluation, clip check, experiment log
├── configs/            unified classes and dataset-specific mappings
├── models/             trained weights (public-data models only)
├── results/            experiment log, confusion matrices, filter, crop and ESPDet results
└── data/               (not versioned) datasets and team recordings
firmware/
├── lib/icvsp_core/     device logic: IMU detector, event builder, signer, replay guard
├── lib/icvsp_sim/      simulated IMU and GNSS
├── test/               30 unit tests (PlatformIO native)
├── tools/              end-to-end check with the engine
└── espdet_qemu/        ESPDet-Pico on the ESP32-S3 (ESP-IDF + ESP-DL), run in QEMU
```

## Roadmap

1. Own recordings (phone fixed in the car: video, accelerometer, GPS) as a labelled Egyptian test set
2. Tune the IMU detector on those recordings; camera + IMU fusion
3. First boards: ESP32-S3 (IMU unit, ESPDet-Pico on-chip test) and Raspberry Pi 5 + Hailo vs Jetson Orin Nano benchmark
4. Engine: low-density (1–2 user) and insider-attack improvements; burst packet loss
5. Vehicle-to-vehicle messaging over ESP32 (ESP-NOW) and cloud synchronisation (AWS IoT)
6. Field test: one unit on a fixed bus route, one rotating between volunteer cars

## Credits and licences

- Dataset: C. Liang et al., "SBP-YOLO: a lightweight real-time model for detecting speed bumps and potholes
  toward intelligent vehicle suspension systems", *J. Real-Time Image Processing* 23, 52 (2026). Not
  redistributed here; downloaded from the authors' public folder.
- Detector: [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) (AGPL-3.0).
- ESPDet-Pico and its deployment tools: Espressif [esp-detection](https://github.com/espressif/esp-detection),
  [ESP-PPQ](https://github.com/espressif/esp-ppq), [ESP-DL](https://github.com/espressif/esp-dl) and ESP-IDF.
- Road segmentation: SegFormer-B0 trained on Cityscapes (NVIDIA, via Hugging Face).
- Signatures: [Monocypher](https://monocypher.org) 4.0.2 (BSD-2 / CC0), included in `firmware/lib/monocypher`.
