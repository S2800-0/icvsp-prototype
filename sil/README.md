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
