"""Load test: the real backend over HTTP while the stored data grows.

Simulated cars around Cairo upload position batches (1 Hz, 60 s per batch) and signed hazard reports, as phones
would. At each stage the test measures how long the backend takes to answer:
  POST /reports   (signature, checks, storage)
  POST /beacons   (a 60-position batch)
  GET  /hazards   (a driver asking what is ahead)

    python3 sil/load_test.py                          # stages up to 400 cars -> sil/results/load_test.md
    python3 sil/load_test.py --stages 50 100 --concurrency 10

Rate limits are switched off here (every simulated car shares one address); they are tested separately.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from icvsp_backend.app import create_app  # noqa: E402
from icvsp_backend.auth import issue_dev_token  # noqa: E402
from icvsp_backend.store import Store  # noqa: E402
from icvsp.geo import offset  # noqa: E402
from icvsp.model import to_iso  # noqa: E402

JWT, ADMIN = "load-test-secret", {"X-Admin-Token": "load-admin"}
LAT0, LON0 = 30.0444, 31.2357
OFF = {k: None for k in ("reports_ip", "reports_device", "beacons_user", "hazards_user", "auth_fail_ip", "admin")}


class Car:
    def __init__(self, vid: str, rng: random.Random):
        self.id, self.seq = vid, 0
        self.key = Ed25519PrivateKey.generate()
        self.pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.lat, self.lon = offset(LAT0, LON0, rng.uniform(-4000, 4000), rng.uniform(-4000, 4000))
        self.heading = rng.choice([0.0, 90.0, 180.0, 270.0])
        self.auth = {"Authorization": "Bearer " + issue_dev_token(JWT, f"user-{vid}", "driver", vid)}

    def drive(self, t0: float, seconds: int = 60, speed: float = 11.0) -> list[dict]:
        rows = []
        for k in range(seconds):
            north = speed * k if self.heading == 0 else -speed * k if self.heading == 180 else 0.0
            east = speed * k if self.heading == 90 else -speed * k if self.heading == 270 else 0.0
            lat, lon = offset(self.lat, self.lon, north, east)
            rows.append({"vehicle_id": self.id, "t": t0 + k, "lat": lat, "lon": lon, "speed_mps": speed,
                         "heading_deg": self.heading})
        self.lat, self.lon = rows[-1]["lat"], rows[-1]["lon"]
        return rows

    def report(self, t: float, lat: float, lon: float, typ: str) -> bytes:
        self.seq += 1
        ev = {"schema_version": 2, "event_id": f"{self.id}-1-{self.seq}", "device_id": self.id, "boot_id": 1,
              "sequence": self.seq, "type": typ, "detected_at": to_iso(t), "gps_valid": True, "latitude": lat,
              "longitude": lon, "position_accuracy_m": 3.5, "speed_kmh": 40.0, "heading_deg": self.heading,
              "severity": "high", "detection_confidence": 0.9, "imu_peak_g": 0.8, "model": "imu-v0"}
        payload = json.dumps(ev, separators=(",", ":"))
        ms = int(t * 1000)
        msg = b"ICVSP-v1\0" + self.id.encode() + b"\0" + ev["event_id"].encode() + b"\0" + struct.pack(">q", ms)
        sig = self.key.sign(msg + payload.encode()).hex()
        return (f'{{"event":{payload},"sender":"{self.id}","msg_id":"{ev["event_id"]}","utc_ms":{ms},'
                f'"public_key":"{self.pub}","signature":"{sig}"}}').encode()


def pct(v: list[float], q: float) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


async def timed(client, sem, method, url, **kw) -> tuple[float, int]:
    async with sem:
        t = time.perf_counter()
        r = await client.request(method, url, **kw)
        return (time.perf_counter() - t) * 1000, r.status_code


async def stage(base: str, cars: list[Car], new: list[Car], hazards: list[tuple], rng: random.Random, conc: int,
                t0: float) -> dict:
    """One more minute of driving, starting at t0 (each stage follows the previous one, as a real drive does)."""
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(base_url=base, timeout=120) as c:
        for car in new:
            await c.post("/devices", json={"device_id": car.id, "public_key": car.pub, "vetted": True}, headers=ADMIN)
        # every car uploads its last minute of positions
        beacon = await asyncio.gather(*[timed(c, sem, "POST", "/beacons", json=car.drive(t0), headers=car.auth)
                                        for car in cars])
        # each new car reports two hazards near where it is (some shared hazards, some new)
        jobs = []
        for car in new:
            for _ in range(2):
                if hazards and rng.random() < 0.5:
                    lat, lon, typ = rng.choice(hazards)
                else:
                    lat, lon = offset(car.lat, car.lon, rng.uniform(-20, 20), rng.uniform(-20, 20))
                    typ = rng.choice(["pothole", "speed_bump"])
                    hazards.append((lat, lon, typ))
                jobs.append(timed(c, sem, "POST", "/reports", content=car.report(t0 + 59 - rng.uniform(0, 5), lat, lon, typ)))
        report = await asyncio.gather(*jobs)
        # 40 drivers ask what is ahead, at the same time
        asks = [timed(c, sem, "GET", "/hazards", params={"lat": car.lat, "lon": car.lon, "heading_deg": car.heading},
                      headers=car.auth) for car in rng.sample(cars, min(40, len(cars)))]
        t0 = time.perf_counter()
        query = await asyncio.gather(*asks)
        wall = time.perf_counter() - t0
    errors = sum(code >= 400 for _, code in beacon + report + query)
    q = [ms for ms, _ in query]
    return {"cars": len(cars), "reports": len(cars) * 2, "positions": len(cars) * 60,
            "report_ms": statistics.median(ms for ms, _ in report), "beacons_ms": statistics.median(ms for ms, _ in beacon),
            "hazards_ms": statistics.median(q), "hazards_p95_ms": pct(q, 0.95), "hazards_per_s": len(q) / wall,
            "errors": errors}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", type=int, nargs="+", default=[25, 50, 100, 200, 400])
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--out", default=str(ROOT / "sil" / "results" / "load_test.md"))
    a = ap.parse_args()
    rng = random.Random(1)
    db = Path(tempfile.mkdtemp()) / "load.sqlite3"
    app = create_app(Store(str(db)), admin_token="load-admin", jwt_secret=JWT, rate_limits=OFF)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{a.port}"
    cars, hazards, rows = [], [], []
    start = time.time() - 60 * (len(a.stages) + 1)      # stage i is minute i of the drive, all in the past
    for i, n in enumerate(a.stages):
        new = [Car(f"car-{k:04d}", rng) for k in range(len(cars), n)]
        cars += new
        r = asyncio.run(stage(base, cars, new, hazards, rng, a.concurrency, start + 60 * i))
        rows.append(r)
        print(f"{r['cars']:4d} cars | report {r['report_ms']:7.1f} ms | 60 positions {r['beacons_ms']:7.1f} ms | "
              f"hazards {r['hazards_ms']:8.1f} ms (p95 {r['hazards_p95_ms']:8.1f}) | {r['hazards_per_s']:6.1f} queries/s | "
              f"errors {r['errors']}", flush=True)
    server.should_exit = True
    lines = ["# Backend load test", "",
             f"Generated by `sil/load_test.py`. One backend process (uvicorn, SQLite file) on a laptop; cars around Cairo "
             f"upload a minute of positions each, report hazards (signed, Ed25519) and 40 drivers ask `GET /hazards` "
             f"at the same time ({a.concurrency} requests in flight). Median response times; rate limits off.", "",
             "| Cars | Reports stored | Positions uploaded in the minute | POST /reports | POST /beacons (60) | GET /hazards | GET /hazards p95 | Hazard queries / s | Errors |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['cars']} | {r['reports']} | {r['positions']} | {r['report_ms']:.0f} ms | {r['beacons_ms']:.0f} ms | "
                     f"{r['hazards_ms']:.0f} ms | {r['hazards_p95_ms']:.0f} ms | {r['hazards_per_s']:.1f} | {r['errors']} |")
    Path(a.out).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    main()
