# Pothole confirmation rule (camera decision logic)

Diagnosis: on the held-out Cairo videos the detector often sees a pothole in only **2 processed frames** (15 fps,
forward camera): it passes the camera fast. The decision logic required 3 of 5 frames for every hazard (a rule made
against false speed bumps), so it dropped most detected potholes (v5: 6 of 6 found in the raw detections, 2 kept).

Change: a pothole track is also confirmed in **2 of 5 frames when its best box has confidence ≥ 0.4**
(`TrackerConfig::pothole_k_hits`, `pothole_min_peak_conf`). The rule only adds to 3 of 5; speed bumps are unchanged
(3 frames and the motion check). Chosen on the night video, checked on the day video; replayed with
`firmware/.pio/replay_detections ... --forward` (`--pothole-k 0` = before).

| Video | Model | Rule | Potholes found | Pothole false / min | Speed bumps found | Speed bump false / min |
|---|---|---|---|---|---|---|
| Night (held out, used to choose) | ESPDet-Pico (chip) | before: 3 of 5 | 1/6 | 0.50 | 2/5 | 0.70 |
| Night (held out, used to choose) | ESPDet-Pico (chip) | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 2/6 | 0.50 | 2/5 | 0.70 |
| Night (held out, used to choose) | YOLO11n v5 | before: 3 of 5 | 2/6 | 0.70 | 0/5 | 0.00 |
| Night (held out, used to choose) | YOLO11n v5 | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 5/6 | 1.20 | 0/5 | 0.00 |
| Night (held out, used to choose) | YOLO11n v3c | before: 3 of 5 | 2/6 | 0.80 | 0/5 | 0.30 |
| Night (held out, used to choose) | YOLO11n v3c | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 3/6 | 1.00 | 0/5 | 0.30 |
| Day (held out, check) | ESPDet-Pico (chip) | before: 3 of 5 | 0/2 | 0.11 | 0/3 | 0.00 |
| Day (held out, check) | ESPDet-Pico (chip) | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 0/2 | 0.17 | 0/3 | 0.00 |
| Day (held out, check) | YOLO11n v5 | before: 3 of 5 | 0/2 | 0.56 | 0/3 | 0.00 |
| Day (held out, check) | YOLO11n v5 | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 0/2 | 0.67 | 0/3 | 0.00 |
| Day (held out, check) | YOLO11n v3c | before: 3 of 5 | 0/2 | 0.89 | 0/3 | 0.00 |
| Day (held out, check) | YOLO11n v3c | after: 3 of 5, or 2 of 5 with a box ≥ 0.4 | 0/2 | 0.94 | 0/3 | 0.00 |

Effect: more potholes found, with somewhat more false pothole reports. Camera-only reports have no measured size, so
they can raise an advisory at most (FR-27); a warning still needs an IMU-measured report. The day video's 2 potholes
are found by no model under any rule (a detector limit, not a logic one). Small samples (8 potholes in 28 minutes):
provisional until the team's own recordings, where the threshold is re-checked.
