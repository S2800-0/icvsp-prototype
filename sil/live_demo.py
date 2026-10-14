"""Live demo: the system simulation played in (sped-up) real time against the real backend over HTTP, with the
operator dashboard showing it as it happens.

    python3 sil/live_demo.py                          # attacks scenario, 30 cars, 10x speed
    python3 sil/live_demo.py --scenario faults --speed 20 --cars 40

It starts the backend (uvicorn, in memory, nothing written to disk) on 127.0.0.1, builds and runs the C++ device
code for every car (same as run_sil.py), then sends each report, position beacon, hazard query and settle call
over HTTP when the simulated clock reaches it. The dashboard opens in the browser; from there you can pause,
change speed, and launch extra attacks (replay, tampering, unknown device, a fake pothole, a Sybil group) to see
how the backend and the trust engine answer.

The backend clock is the simulated clock, so time-based rules (hazard ageing, plausibility, settle) behave as
they would in real time.
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
import struct
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_sil  # noqa: E402
from run_sil import HAZARDS, ROAD_M, SCENARIOS, WARN_CHECK_M, position  # noqa: E402

from icvsp_backend.app import create_app  # noqa: E402
from icvsp_backend.store import Store  # noqa: E402
from icvsp.model import to_iso  # noqa: E402


class SimClock:
    """Simulated time: runs `speed` times faster than the wall clock and can be paused."""

    def __init__(self, utc0: float, speed: float):
        self.utc0, self.speed, self.paused = utc0, speed, False
        self._rel, self._wall = -2.0, time.monotonic()
        self._lock = threading.Lock()

    def rel(self) -> float:
        with self._lock:
            return self._rel + (0.0 if self.paused else (time.monotonic() - self._wall) * self.speed)

    def __call__(self) -> float:
        return self.utc0 + self.rel()

    def set(self, speed: float | None = None, paused: bool | None = None) -> None:
        with self._lock:
            now = time.monotonic()
            if not self.paused:
                self._rel += (now - self._wall) * self.speed
            self._wall = now
            if speed is not None:
                self.speed = speed
            if paused is not None:
                self.paused = paused


class Signer:
    """A device identity made up on the spot for an injected attack (same signed-bytes layout as the firmware)."""

    def __init__(self, device_id: str):
        self.id, self.boot, self.seq = device_id, 1, 0
        self.key = Ed25519PrivateKey.generate()
        self.public_hex = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def report(self, t: float, lat: float, lon: float, type_: str, heading: float) -> str:
        self.seq += 1
        ev = {"schema_version": 2, "event_id": f"{self.id}-{self.boot}-{self.seq}", "device_id": self.id,
              "boot_id": self.boot, "sequence": self.seq, "type": type_, "detected_at": to_iso(t),
              "gps_valid": True, "latitude": lat, "longitude": lon, "position_accuracy_m": 3.5,
              "speed_kmh": 45.0, "heading_deg": heading, "severity": "high", "detection_confidence": 0.9,
              "model": "imu-v0"}
        payload = json.dumps(ev, separators=(",", ":"))
        ms = int(t * 1000)
        msg = b"ICVSP-v1\0" + self.id.encode() + b"\0" + ev["event_id"].encode() + b"\0" + struct.pack(">q", ms)
        sig = self.key.sign(msg + payload.encode()).hex()
        return (f'{{"event":{payload},"sender":"{self.id}","msg_id":"{ev["event_id"]}","utc_ms":{ms},'
                f'"public_key":"{self.public_hex}","signature":"{sig}"}}')


class Control(BaseModel):
    speed: float | None = None
    paused: bool | None = None


class Inject(BaseModel):
    kind: str   # replay | tamper | unknown | fake | sybil


class Demo:
    def __init__(self, args):
        self.scn = SCENARIOS[args.scenario]
        if args.cars:
            self.scn.cars = args.cars
        self.token = secrets.token_urlsafe(9)
        run_sil.ADMIN["X-Admin-Token"] = self.token
        self.base = f"http://127.0.0.1:{args.port}"
        print("building the device code and running it for every car ...", flush=True)
        run_sil.build()
        self.utc0 = float(int(time.time()))
        self.run = run_sil.prepare(self.scn, args.seed, self.utc0)
        self.end = max(t for t, _, _ in self.run.events) + 5
        self.clock = SimClock(self.utc0, args.speed)
        self.results, self.queries = run_sil.new_results(), []
        self.log: deque[dict] = deque(maxlen=300)
        self.seq = 0
        self.accepted_lines: list[str] = []
        self.injected = 0
        self.lock = threading.Lock()
        self.store = Store()
        self.app = create_app(self.store, self.clock, admin_token=self.token)
        self._routes()

    # ---------- demo-only routes, added to the normal backend app ----------
    def _routes(self) -> None:
        app = self.app

        def admin(token: str | None) -> None:
            if token != self.token:
                raise HTTPException(401, "admin token required")

        from fastapi import Header

        @app.get("/demo/state", include_in_schema=False)
        def state(after: int = 0, x_admin_token: str | None = Header(default=None)):
            admin(x_admin_token)
            with self.lock:
                log = [r for r in self.log if r["seq"] > after]
            real = [q for q in self.queries if not q["fake"]]
            fake = [q for q in self.queries if q["fake"]]
            late = [q for q in real if q["t"] > 600]
            n = lambda qs, lv: sum(q["level"] == lv for q in qs)  # noqa: E731
            lanes = {"E": 90, "W": 270}
            return {
                "scenario": self.scn.name, "title": self.scn.title, "cars": self.scn.cars,
                "t": self.clock.rel(), "end": self.end, "speed": self.clock.speed, "paused": self.clock.paused,
                "finished": self.clock.rel() >= self.end,
                "road": {"start": position("E", 0), "end": position("E", ROAD_M), "length_m": ROAD_M},
                "truth": [{"type": t, "lat": position("E", p)[0], "lon": position("E", p)[1],
                           "heading_deg": lanes[l], "real": True} for t, p, l in HAZARDS]
                + [{"type": t, "lat": position("E", p)[0], "lon": position("E", p)[1], "heading_deg": lanes[l],
                    "real": False} for t, p, l in self.run.fakes],
                "attackers": [c.vid for c in self.run.attackers],
                "reports": self.results,
                "score": {"real_queries": len(real), "real_warning": n(real, "warning"),
                          "real_advisory": n(real, "advisory"), "real_none": n(real, "none"),
                          "late_queries": len(late), "late_warning": n(late, "warning"),
                          "fake_queries": len(fake), "fake_warning": n(fake, "warning"),
                          "false_warning_queries": sum(q["false_warnings"] > 0 for q in self.queries),
                          "warn_check_m": WARN_CHECK_M},
                "log": log,
            }

        @app.post("/demo/control", include_in_schema=False)
        def control(c: Control, x_admin_token: str | None = Header(default=None)):
            admin(x_admin_token)
            if c.speed is not None and not 0.1 <= c.speed <= 200:
                raise HTTPException(400, "speed must be between 0.1 and 200")
            self.clock.set(c.speed, c.paused)
            return {"speed": self.clock.speed, "paused": self.clock.paused}

        @app.post("/demo/inject", include_in_schema=False)
        def inject(i: Inject, x_admin_token: str | None = Header(default=None)):
            admin(x_admin_token)
            if i.kind not in ("replay", "tamper", "unknown", "fake", "sybil"):
                raise HTTPException(400, "unknown attack")
            threading.Thread(target=self._inject, args=(i.kind,), daemon=True).start()
            return {"started": i.kind}

    def record(self, rec: dict | None) -> None:
        if rec is None:
            return
        with self.lock:
            self.seq += 1
            rec["seq"] = self.seq
            self.log.append(rec)

    # ---------- the scenario, played in simulated real time ----------
    def player(self) -> None:
        with httpx.Client(base_url=self.base, timeout=30) as client:
            for t_rel, kind, payload in self.run.events:
                while self.clock.rel() < t_rel:
                    time.sleep(0.02)
                rec = run_sil.apply(client, self.run, t_rel, kind, payload, self.results, self.queries)
                if rec and rec["kind"] == "report" and rec["result"] == "accepted":
                    self.accepted_lines.append(payload)
                if rec and rec["kind"] == "query" and rec["level"] == "none" and not rec["fake"]:
                    rec["result"] += " (no hazard known yet)"
                self.record(rec)
        self.record({"t": self.clock.rel(), "kind": "info", "result": "scenario finished"})

    # ---------- attacks launched from the dashboard ----------
    def _inject(self, kind: str) -> None:
        self.injected += 1
        n = self.injected
        now, t_rel = self.clock(), self.clock.rel()
        rng = random.Random(n)
        with httpx.Client(base_url=self.base, timeout=30) as c:
            def send(line: str, what: str) -> None:
                r = c.post("/reports", content=line.encode(), headers={"Content-Type": "application/json"})
                body = r.json()
                res = body.get("status") if r.status_code == 201 else "rejected: " + body.get("detail", "")
                self.record({"t": t_rel, "kind": "attack", "device": json.loads(line)["sender"], "http": r.status_code,
                             "result": f"{what}: {res}"})

            def drive(vid: str, at_m: float) -> None:
                """Beacons of a car that drove east up to `at_m` over the last 20 s (a plausible trace)."""
                rows = []
                for k in range(21):
                    lat, lon = position("E", max(0.0, at_m - 12.5 * (20 - k)))
                    rows.append({"vehicle_id": vid, "t": now - 20 + k, "lat": lat, "lon": lon, "speed_mps": 12.5,
                                 "heading_deg": 90})
                c.post("/beacons", json=rows)

            if kind in ("replay", "tamper"):
                if not self.accepted_lines:
                    self.record({"t": t_rel, "kind": "attack", "result": f"{kind}: no accepted report to copy yet"})
                    return
                line = self.accepted_lines[-1]
                if kind == "replay":
                    send(line, "replayed an accepted report")
                else:
                    send(line.replace('"severity":"high"', '"severity":"low"').replace(
                        '"severity":"medium"', '"severity":"low"'), "changed the severity of a signed report")
            elif kind == "unknown":
                s = Signer(f"stranger-{n}")
                at = rng.uniform(300, ROAD_M - 300)
                send(s.report(now, *position("E", at), "pothole", 90), "report from an unregistered device")
            elif kind == "fake":
                s = Signer(f"newcar-{n}")
                c.post("/devices", json={"device_id": s.id, "public_key": s.public_hex}, headers=run_sil.ADMIN)
                at = rng.uniform(300, ROAD_M - 300)
                drive(s.id, at)
                send(s.report(now, *position("E", at), "pothole", 90),
                     f"new device reports a pothole that does not exist at {at:.0f} m")
            elif kind == "sybil":
                at = rng.uniform(300, ROAD_M - 300)
                for k in range(5):
                    s = Signer(f"clone{n}-{k}")
                    c.post("/devices", json={"device_id": s.id, "public_key": s.public_hex}, headers=run_sil.ADMIN)
                    drive(s.id, at)
                    send(s.report(now, *position("E", at), "speed_bump", 90),
                         f"Sybil identity {k + 1}/5 reports a speed bump that does not exist at {at:.0f} m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="attacks")
    ap.add_argument("--cars", type=int, default=30)
    ap.add_argument("--speed", type=float, default=10.0, help="simulated seconds per real second")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    demo = Demo(a)
    server = uvicorn.Server(uvicorn.Config(demo.app, host="127.0.0.1", port=a.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    url = f"{demo.base}/dashboard#token={demo.token}"
    print(f"\n  dashboard: {url}\n  API docs:  {demo.base}/docs\n  Ctrl+C to stop\n", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    threading.Thread(target=demo.player, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
