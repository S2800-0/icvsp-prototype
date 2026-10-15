# System simulation (software in the loop)

The whole ICVSP chain without hardware, so the system can be developed and verified before (or without) the
board:

road with real potholes and speed bumps → many vehicles, **each running the real C++ device code**
(`firmware/lib/icvsp_core`, built for the laptop) on simulated IMU, GNSS and rear-camera detections →
signed v2 reports → phone (BLE loss, upload delays, internet outages, retries) → **backend API** →
**trust engine** → following vehicles ask `GET /hazards` 300 m before each hazard.

```bash
python3 sil/run_sil.py                         # 4 scenarios, 40 cars, about 2.5 minutes -> sil/results/
python3 sil/run_sil.py --scenario attacks --cars 60 --seed 3
python3 -m unittest sil/test_sil.py -v         # system test used in CI (about 30 s)
```

Scenarios: all devices new; 30 % vetted fleet; fleet with faults (20 % BLE loss, 10-minute internet outage,
GPS loss on 20 % of cars); fleet with attacks (a registered device inventing a pothole, five Sybil identities
inventing a speed bump, a replayed report, a tampered report, an unregistered device, a revoked device).

**What is real and what is assumed.** Real: the device code, the backend checks, the trust engine and the
hazard query. Assumed: the sensor signals (synthetic), how often the camera and the IMU catch each hazard
(camera rates from the chip model with the device logic on held-out night video), and radio and network
behaviour. The results show how the system logic behaves under those assumptions, not road performance.
Replacing the assumptions with measured values (phone recordings now, the board later) is the next step.

Results: [`results/summary.md`](results/summary.md).

## Which camera model helps the system most

```bash
python3 sil/compare_cameras.py                 # same scenarios with each camera model's measured rates
```

Each camera model's hit rate and false-report rate after the device logic, measured on the held-out Cairo videos,
is plugged into the simulation (`CAMERAS` in `run_sil.py`). Results: [`results/camera_comparison.md`](results/camera_comparison.md).

## Live demo with the dashboard

```bash
python3 sil/live_demo.py                       # attacks scenario, 30 cars, 10x speed; opens the dashboard
python3 sil/live_demo.py --scenario faults --speed 20 --cars 40
```

The same scenario, played in sped-up real time against the **real backend over HTTP** (uvicorn on
127.0.0.1, in memory, nothing saved). The backend's operator dashboard opens in the browser and shows the
road, the cars, the true hazards next to the trust engine's decisions (advisory / warning / suppressed, with
its reasons), device reliability, and every report, rejection and warning check as it happens. Buttons on the
page launch extra attacks (replay, tampering, unregistered device, a new device inventing a pothole, a Sybil
group) so the answer of the backend and the trust engine can be shown live.
