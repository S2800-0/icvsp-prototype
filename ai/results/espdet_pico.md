# ESPDet-Pico at 224 px: a low-cost ESP32-S3 camera tier? (experiment, 2 Oct 2026)

Espressif's ESPDet-Pico (0.36 M parameters, 0.17 GFLOPs at 224 × 224, about 126 ms per frame on an
ESP32-S3) trained from scratch for 300 epochs on the same data as v3c. Scored exactly like v3c.

| Public test (1,104 images) | v3c (YOLO11n, 960 px, 8.3 GFLOPs on video) | ESPDet-Pico (224 px, 0.17 GFLOPs) |
|---|---|---|
| Overall mAP50 | 0.846 | 0.533 |
| Speed bump mAP50 (P / R) | 0.937 (0.92 / 0.91) | 0.790 (0.76 / 0.76) |
| Pothole mAP50 (P / R) | 0.754 (0.84 / 0.68) | 0.277 (0.61 / 0.23) |

Held-out Egyptian video, confidence ≥ 0.25, road and motion checks on speed bumps:

| | v3c | ESPDet-Pico |
|---|---|---|
| Night: potholes found | 4 / 6 (incl. confirmed 4:03, 7:47) | 3 / 6 (misses confirmed 4:03) |
| Night: bumps passing both checks | 0 / 5 | 2 / 5 (7:47, 8:23; chance 12 %) |
| Night: false flags / min (pothole, bump) | 1.20, 0.10 | 0.90, 0.70 |
| Day: false flags / min (pothole, bump) | 1.00, 0.00 | 0.33, 0.00 (nearly silent) |

- Potholes collapse at 224 px (recall 0.23 on the public test): too few pixels for small, distant holes.
- Speed bumps hold up reasonably (0.79 mAP50). On the night video, two bump detections pass both checks;
  by eye they sit on a raised ridge across the lane (7:47) and a dark strip across the lane at the start of
  the tagged bump series (8:23). These are the first plausible camera bump detections on held-out video,
  but 2 of 5 against a 12 % chance level is not conclusive.
- The model was still improving at epoch 300 (Espressif trains for 1,200).

**Conclusion:** at 224 px ESPDet-Pico is not usable for potholes, but it is a credible speed-bump camera
for a low-cost Lite tier, alongside the IMU. Next: the 320 px run, longer training, and an on-chip test.
