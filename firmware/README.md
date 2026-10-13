# ICVSP device firmware

The device-side logic of the ICVSP unit (Basic tier: ESP32-S3 + IMU + GNSS), written so it can be built and
tested on a laptop before we have a board. Built with [PlatformIO](https://platformio.org).

| Module | What it does | Requirement |
|---|---|---|
| `lib/icvsp_core/src/bump_detector` | Detects potholes and speed bumps from the vertical acceleration | FR-27 (proposed) |
| `lib/icvsp_core/src/hazard_tracker` | Camera decision logic: confidence, road region, tracking, 3-of-5 frames, motion check for speed bumps (forward or rear camera) | FR-01 |
| `lib/icvsp_core/src/fusion` | Combines IMU and camera into one report per hazard; the IMU decides the type when they disagree | FR-27 (proposed) |
| `lib/icvsp_core/src/event_builder` | Builds the safety event, message format v2 (`engine/schema`): event ID = device-boot-sequence, `gps_valid` with null position when there is no fix, IMU jolt in g | FR-01/FR-02 |
| `lib/icvsp_core/src/device` | The whole device pipeline (detectors → fusion → event → signature) in one class, used by the demo, the system simulation and later the board | — |
| `tools/sil_vehicle.cpp` | One simulated vehicle for the system simulation (`sil/`) | — |
| `lib/icvsp_core/src/signer` | Ed25519 signature over sender, message ID, time and event | NFR-10 (proposed) |
| `lib/icvsp_core/src/replay_guard` | Rejects stale (> 30 s), future-dated and repeated messages | FR-06 |
| `lib/icvsp_sim` | Simulated IMU and GNSS: a car driving over chosen hazards | – |
| `lib/monocypher` | [Monocypher 4.0.2](https://monocypher.org) (BSD-2 / CC0), standard Ed25519 | – |

`icvsp_core` makes no hardware calls, so the same code runs on the laptop and on the ESP32-S3.

## Run it

```bash
pip install platformio cryptography
cd firmware
pio test -e native                         # unit tests (52)
pio run -e native && .pio/build/native/program   # demo drive: prints signed reports as JSON
python tools/check_with_engine.py          # builds the demo, then checks its reports from the backend side
```

The first run downloads about 10 MB (PlatformIO's native platform, build tool and the Unity test framework).
Only a C++ compiler is needed (Apple clang or gcc).

`check_with_engine.py` validates every report with the trust engine's own validation and verifies the
signature with an independent Ed25519 library (Python `cryptography`), so the firmware and the backend are
known to agree on the format and the signed bytes.

## Accuracy with the decision logic

`tools/score_logic.py` runs the same tracker code over the detections saved from the two held-out Cairo videos
and scores it against the tagged hazards (results in `results/logic_accuracy.md`). On the night video the logic
cuts false speed-bump reports from 3.2 to 0.3 per minute (YOLO11n v3c) and from 3.5 to 0.7 (ESPDet-Pico), and
false pothole reports from 1.2 to 0.8. It also drops real detections: potholes found go from 4 to 2 of 6 (the two
kept are the hits confirmed by eye), and camera speed bumps from 3 to 0 of 5 (v3c) and 4 to 2 of 5 (ESPDet-Pico),
which supports the IMU as the main speed-bump sensor. The simple road region gives the same result as the
SegFormer road mask, so the chip does not need a segmentation model. Thresholds were fixed before scoring;
8 potholes and 8 bumps are too few for final conclusions.

## How the IMU detector works

The slow part of the vertical acceleration (gravity, slopes, the nose dipping when braking) is removed with a
running average; what is left is the jolt. A jolt is a hazard when it is at least 5 times the car's usual
vibration on that road and at least 2 m/s², while the car moves faster than 8 km/h. Downward first means
pothole (the wheel drops); upward first and lasting at least 0.2 s means speed bump. One report per hazard
(front and rear wheels).

**All thresholds are first guesses.** The tests use synthetic signals; the values have to be tuned on real
recordings from a phone or the board on Egyptian roads.

## Signed messages

The signature covers

    "ICVSP-v1" 0x00 sender 0x00 msg_id 0x00 utc_ms (8 bytes, big-endian) event-JSON

The event JSON is written in one fixed format, so its bytes are the canonical form. The receiver checks the
signature first and only then the replay window, so forged messages cannot fill the replay table. The
private key handling here is a demo (fixed public seed); on the board the key belongs in encrypted flash with
secure boot, and key provisioning and registration are part of the cybersecurity work.

## Known gaps

- No board environment yet: uncomment `[env:xiao_esp32s3]` in `platformio.ini` once we have a board (the
  first build downloads the ESP32 toolchain, several hundred MB).
- `road_segment` is a coarse grid cell (about 200 m) until the backend maps reports to real roads.
