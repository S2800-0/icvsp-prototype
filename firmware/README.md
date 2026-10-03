# ICVSP device firmware

The device-side logic of the ICVSP unit (Basic tier: ESP32-S3 + IMU + GNSS), written so it can be built and
tested on a laptop before we have a board. Built with [PlatformIO](https://platformio.org).

| Module | What it does | Requirement |
|---|---|---|
| `lib/icvsp_core/src/bump_detector` | Detects potholes and speed bumps from the vertical acceleration | FR-27 (proposed) |
| `lib/icvsp_core/src/event_builder` | Builds the safety event in the engine's format (`engine/schema`) | FR-01/FR-02 |
| `lib/icvsp_core/src/signer` | Ed25519 signature over sender, message ID, time and event | NFR-10 (proposed) |
| `lib/icvsp_core/src/replay_guard` | Rejects stale (> 30 s), future-dated and repeated messages | FR-06 |
| `lib/icvsp_sim` | Simulated IMU and GNSS: a car driving over chosen hazards | – |
| `lib/monocypher` | [Monocypher 4.0.2](https://monocypher.org) (BSD-2 / CC0), standard Ed25519 | – |

`icvsp_core` makes no hardware calls, so the same code runs on the laptop and on the ESP32-S3.

## Run it

```bash
pip install platformio cryptography
cd firmware
pio test -e native                         # unit tests (30)
pio run -e native && .pio/build/native/program   # demo drive: prints signed reports as JSON
python tools/check_with_engine.py          # builds the demo, then checks its reports from the backend side
```

The first run downloads about 10 MB (PlatformIO's native platform, build tool and the Unity test framework).
Only a C++ compiler is needed (Apple clang or gcc).

`check_with_engine.py` validates every report with the trust engine's own validation and verifies the
signature with an independent Ed25519 library (Python `cryptography`), so the firmware and the backend are
known to agree on the format and the signed bytes.

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
