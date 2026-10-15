"""Software-in-the-loop (SIL) simulation of the whole ICVSP chain, without hardware.

    road with real hazards -> many vehicles, each running the real C++ device code (firmware/lib/icvsp_core,
    built natively) on simulated IMU, GNSS and rear-camera detections -> signed v2 reports -> phone
    (BLE loss, upload delay, internet outages, retries) -> backend API (signature, registry, replay,
    schema) -> trust engine -> other vehicles ask GET /hazards before reaching each hazard.

    python3 sil/run_sil.py                    # all scenarios -> sil/results/
    python3 sil/run_sil.py --scenario attacks --cars 60 --seed 3

What is real and what is assumed:
  real    : device code (IMU detector, camera decision logic, fusion, event format, Ed25519), backend checks,
            trust engine, hazard query.
  assumed : sensor signals (synthetic), how often the camera / IMU catches each hazard (DETECTION below,
            taken from the held-out-video results where they exist), radio and network delays and losses.
Results therefore show how the system logic behaves under these assumptions, not road performance.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.testclient import TestClient  # noqa: E402

from icvsp_backend.app import create_app  # noqa: E402
from icvsp_backend.store import Store  # noqa: E402
from icvsp.model import from_iso  # noqa: E402

FW = ROOT / "firmware"
BIN = FW / ".pio" / "sil_vehicle"
ADMIN = {"X-Admin-Token": "sil"}

LAT0, LON0 = 30.0444, 31.2357          # west end of the simulated road (Cairo)
ROAD_M = 3000.0
UTC0 = 1790000000.0                    # simulation start, Unix seconds

# hazards: (type, position from the west end in m, lane "E" eastbound or "W" westbound)
HAZARDS = [("pothole", 600, "E"), ("speed_bump", 1200, "E"), ("pothole", 2100, "E"),
           ("pothole", 900, "W"), ("speed_bump", 1800, "W"), ("pothole", 2600, "W")]

# probability that a passing vehicle's sensor catches a hazard (ASSUMED; camera values from the chip model
# with the device logic on held-out night video, E-05: bumps 2/5, potholes 1/6)
DETECTION = {
    "imu": {"speed_bump": 0.95, "pothole": 0.6},    # a wheel can miss a pothole; every wheel rolls over a bump
    "cam": {"speed_bump": 0.4, "pothole": 0.17},
}
# Camera profiles: hit rate per passing car and false reports per km AFTER the device logic, measured on the
# held-out Cairo videos (firmware/results/logic_accuracy.md, night + day pooled: 8 bumps, 8 potholes, 28 min).
# False reports per minute of video are converted to per km assuming about 30 km/h city driving (0.5 km/min).
# Small samples: these are rough rates for comparing models, not accuracy claims. "assumed" is the original setting.
CAMERAS = {
    "assumed": {"hit": DETECTION["cam"], "false_per_km": {"speed_bump": 0.0, "pothole": 0.0}},
    # device logic with the pothole rule (3 of 5, or 2 of 5 with a box >= 0.4; firmware/results/pothole_rule.md)
    "espdet": {"hit": {"speed_bump": 2 / 8, "pothole": 2 / 8}, "false_per_km": {"speed_bump": 0.50, "pothole": 0.58}},
    "v3c": {"hit": {"speed_bump": 0 / 8, "pothole": 3 / 8}, "false_per_km": {"speed_bump": 0.21, "pothole": 1.92}},
    "v4": {"hit": {"speed_bump": 0 / 8, "pothole": 3 / 8}, "false_per_km": {"speed_bump": 0.00, "pothole": 1.42}},
    "v5": {"hit": {"speed_bump": 0 / 8, "pothole": 5 / 8}, "false_per_km": {"speed_bump": 0.00, "pothole": 1.72}},
    # before the pothole rule (3 of 5 for every hazard), kept for comparison
    "espdet_3of5": {"hit": {"speed_bump": 2 / 8, "pothole": 1 / 8}, "false_per_km": {"speed_bump": 0.50, "pothole": 0.50}},
    "v5_3of5": {"hit": {"speed_bump": 0 / 8, "pothole": 2 / 8}, "false_per_km": {"speed_bump": 0.00, "pothole": 1.22}},
    "none": {"hit": {"speed_bump": 0.0, "pothole": 0.0}, "false_per_km": {"speed_bump": 0.0, "pothole": 0.0}},
}
FALSE_JOLT_PER_CAR = 0.05      # honest device, false pothole report (e.g. a manhole cover) per drive
FALSE_BOX_PER_KM = 0.5         # static false camera boxes (kerb, shadow); the device logic should reject them
WARN_CHECK_M = 300.0           # a vehicle asks for hazards when it is this far before each hazard
MATCH_M = 15.0                 # a returned hazard matches a real one within this distance


@dataclass
class Faults:
    ble_loss: float = 0.02             # report lost between device and phone
    upload_delay_s: tuple = (0.3, 2.0)
    outage: tuple | None = None        # (start, end) seconds from the start: phones have no internet
    gps_loss_cars: float = 0.0         # share of cars with a 10 s GPS outage
    beacon_loss: float = 0.01


@dataclass
class Scenario:
    name: str
    title: str
    cars: int = 40
    minutes: float = 30.0
    vetted: float = 0.0                # share of devices provisioned with a starting history
    faults: Faults = field(default_factory=Faults)
    attacks: bool = False


SCENARIOS = {
    "new_devices": Scenario("new_devices", "All devices new (no history)"),
    "fleet": Scenario("fleet", "30 % vetted fleet devices", vetted=0.3),
    "faults": Scenario("faults", "Fleet + faults: 20 % BLE loss, 10 min internet outage, GPS loss on 20 % of cars",
                       vetted=0.3, faults=Faults(ble_loss=0.2, outage=(600, 1200), gps_loss_cars=0.2,
                                                 beacon_loss=0.1)),
    "attacks": Scenario("attacks", "Fleet + attacks: fake reporter, 5 Sybil identities, replay, tampering, "
                                   "unregistered and revoked devices", vetted=0.3, attacks=True),
}


# ---------------------------------------------------------------------------------------------------------
def build() -> None:
    src, obj = FW / "lib/icvsp_core/src", FW / ".pio" / "sil"
    obj.mkdir(parents=True, exist_ok=True)
    for c in ("monocypher.c", "monocypher-ed25519.c"):
        subprocess.run(["cc", "-O2", "-c", str(FW / "lib/monocypher/src" / c), "-o", str(obj / (c + ".o"))],
                       check=True)
    subprocess.run(["c++", "-std=c++17", "-O2", f"-I{src}", f"-I{FW / 'lib/icvsp_sim/src'}",
                    f"-I{FW / 'lib/monocypher/src'}", str(FW / "tools/sil_vehicle.cpp"),
                    *map(str, sorted(src.glob("*.cpp"))), *map(str, sorted(obj.glob("*.o"))), "-o", str(BIN)],
                   check=True)


def position(direction: str, travelled_m: float) -> tuple[float, float]:
    """Same flat-earth formula as the device simulator's FakeGnss."""
    east = travelled_m if direction == "E" else ROAD_M - travelled_m
    return LAT0, LON0 + east / (111320.0 * math.cos(math.radians(LAT0)))


@dataclass
class Vehicle:
    vid: str
    direction: str
    start: float                       # seconds from simulation start
    speed_kmh: float
    seed: str
    boot: int = 1
    script: list[str] = field(default_factory=list)
    truth: list[tuple] = field(default_factory=list)  # (hazard index, time reached)
    gps_loss: tuple | None = None

    @property
    def v(self) -> float:
        return self.speed_kmh / 3.6

    def travelled(self, pos_m: float) -> float:
        return pos_m if self.direction == "E" else ROAD_M - pos_m

    def run(self, utc0: float = UTC0) -> list[tuple[float, str]]:
        """Runs the device code; returns (emission time from simulation start, signed line)."""
        lat, lon = position(self.direction, 0)
        lines = [f"id {self.vid}", f"boot {self.boot}", f"seed {self.seed}",
                 f"start {int((utc0 + self.start) * 1000)}", f"origin {lat} {lon} {90 if self.direction == 'E' else 270}",
                 f"speed {self.speed_kmh}", f"duration {ROAD_M / self.v + 6:.1f}",
                 f"sim_seed {zlib.crc32(self.vid.encode()) % 100000 + 1}", *self.script]
        if self.gps_loss:
            lines.append(f"gpsloss {self.gps_loss[0]} {self.gps_loss[1]}")
        out = subprocess.run([str(BIN)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        res = []
        for row in out.stdout.splitlines():
            ms, line = row.split("\t", 1)
            res.append((self.start + int(ms) / 1000.0, line))
        return res


def plan(scn: Scenario, rng: random.Random, camera: str = "assumed") -> tuple[list[Vehicle], list[Vehicle]]:
    cam = CAMERAS[camera]
    honest, attackers = [], []
    for k in range(scn.cars):
        d = rng.choice("EW")
        car = Vehicle(f"car-{k:02d}", d, rng.uniform(0, scn.minutes * 60), rng.uniform(30, 60),
                      f"{rng.getrandbits(256):064x}", boot=rng.randint(1, 50))
        for i, (typ, pos, lane) in enumerate(HAZARDS):
            if lane != d:
                continue
            t = car.travelled(pos) / car.v
            car.truth.append((i, car.start + t))
            if rng.random() < DETECTION["imu"][typ]:
                size = rng.uniform(3.0, 5.0) if typ == "speed_bump" else rng.uniform(6.0, 10.0)
                car.script.append(f"imu {typ} {t:.3f} {size:.2f}")
            if rng.random() < cam["hit"][typ]:
                car.script.append(f"cam {typ} {t + 0.6:.3f} {rng.uniform(0.45, 0.75):.2f}")
        if rng.random() < FALSE_JOLT_PER_CAR:
            car.script.append(f"imu pothole {rng.uniform(20, ROAD_M / car.v - 20):.3f} {rng.uniform(6, 9):.2f}")
        for _ in range(int(rng.random() < FALSE_BOX_PER_KM * ROAD_M / 1000)):
            car.script.append(f"falsecam speed_bump {rng.uniform(5, ROAD_M / car.v - 5):.3f} 0.5")
        for typ, per_km in cam["false_per_km"].items():   # false camera reports that pass the device logic
            if per_km > 0:
                for _ in range(int(per_km * ROAD_M / 1000 + rng.random())):
                    car.script.append(f"cam {typ} {rng.uniform(5, ROAD_M / car.v - 5):.3f} {rng.uniform(0.45, 0.75):.2f}")
        if rng.random() < scn.faults.gps_loss_cars and car.truth:
            t = car.truth[0][1] - car.start
            car.gps_loss = (max(0.0, t - 5), t + 5)
        honest.append(car)
    if scn.attacks:
        # a registered device whose owner drives east and reports a pothole that does not exist (at 1500 m)
        fake = Vehicle("fake-01", "E", 300, 45, f"{rng.getrandbits(256):064x}")
        fake.script.append(f"imu pothole {1500 / fake.v:.3f} 9")
        attackers.append(fake)
        # five identities on one car, each reporting a speed bump that does not exist (at 2400 m)
        for k in range(5):
            s = Vehicle(f"sybil-{k}", "E", 900, 50, f"{rng.getrandbits(256):064x}")
            s.script.append(f"imu speed_bump {2400 / s.v:.3f} 4")
            attackers.append(s)
    return honest, attackers


# ---------------------------------------------------------------------------------------------------------
@dataclass
class Run:
    """A scenario as a time-ordered list of events (seconds from the start, kind, payload). The batch run below
    and the live demo (sil/live_demo.py) both play this list against the backend."""
    scn: Scenario
    utc0: float
    honest: list[Vehicle]
    attackers: list[Vehicle]
    emitted: dict[str, list[tuple[float, str]]]
    fakes: list[tuple]
    events: list[tuple]


def prepare(scn: Scenario, seed: int, utc0: float = UTC0, camera: str = "assumed") -> Run:
    rng = random.Random(seed)
    honest, attackers = plan(scn, rng, camera)

    vetted = set(rng.sample([c.vid for c in honest], int(scn.vetted * len(honest))))
    # devices are registered with the public key the device code derived from its seed (seen in its reports)
    events = []
    emitted = {}
    for car in honest + attackers:
        reports = car.run(utc0)
        emitted[car.vid] = reports
        if reports:
            events.append((-1.0, "register", (car.vid, json.loads(reports[0][1])["public_key"], car.vid in vetted)))
        for t_emit, line in reports:
            if rng.random() < scn.faults.ble_loss:
                events.append((t_emit, "lost", car.vid))
                continue
            t_up = t_emit + rng.uniform(*scn.faults.upload_delay_s)
            if scn.faults.outage and scn.faults.outage[0] <= t_up < scn.faults.outage[1]:
                t_up = scn.faults.outage[1] + rng.uniform(0, 30)          # phone queue flushes after the outage
            events.append((t_up, "report", line))
            if rng.random() < 0.05:                                       # timeout: the phone sends it again
                events.append((t_up + rng.uniform(1, 5), "report", line))
        # 1 Hz position beacons from the phone, sent in 10 s batches
        dur = ROAD_M / car.v + 6
        batch = []
        for k in range(int(dur)):
            if rng.random() < scn.faults.beacon_loss:
                continue
            lat, lon = position(car.direction, min(car.v * k, ROAD_M))
            batch.append({"vehicle_id": car.vid, "t": utc0 + car.start + k, "lat": lat, "lon": lon,
                          "speed_mps": car.v, "heading_deg": 90 if car.direction == "E" else 270})
            if len(batch) == 10 or k == int(dur) - 1:
                t_up = car.start + k + 0.5
                if scn.faults.outage and scn.faults.outage[0] <= t_up < scn.faults.outage[1]:
                    t_up = scn.faults.outage[1] + rng.uniform(0, 30)
                events.append((t_up, "beacons", batch))
                batch = []

    if scn.attacks:
        genuine = next(lines[0][1] for lines in emitted.values() if lines)
        events.append((scn.minutes * 30, "report", genuine))                                  # replay
        events.append((scn.minutes * 30, "report", genuine.replace('"severity":"', '"severity":"x')))  # tampered
        stranger = Vehicle("unknown-99", "E", 100, 40, f"{rng.getrandbits(256):064x}")
        stranger.script.append(f"imu pothole {1000 / stranger.v:.3f} 9")
        for t, line in stranger.run(utc0):
            events.append((t + 1, "report", line))                                           # not registered
        # revoke one Sybil identity halfway; its later reports must be refused
        events.append((900 + 30, "revoke", "sybil-4"))

    # each vehicle asks for hazards WARN_CHECK_M before every real hazard in its lane (and before the fakes)
    fakes = [("pothole", 1500, "E"), ("speed_bump", 2400, "E")] if scn.attacks else []
    for car in honest:
        for i, (typ, pos, lane) in enumerate(HAZARDS + fakes):
            if lane != car.direction:
                continue
            d = car.travelled(pos) - WARN_CHECK_M
            if d < 0:
                continue
            events.append((car.start + d / car.v, "query", (car, i)))
    settle_every = 300.0
    t = settle_every
    while t < scn.minutes * 60 + 600:
        events.append((t, "settle", None))
        t += settle_every

    events.sort(key=lambda e: e[0])
    return Run(scn, utc0, honest, attackers, emitted, fakes, events)


def new_results() -> dict:
    return {"accepted": 0, "duplicate": 0, "rejected": {}, "lost": 0}


def apply(client, run: Run, t_rel: float, kind: str, payload, results: dict, queries: list) -> dict | None:
    """Plays one event against the backend (a TestClient or a real HTTP client); returns a log record for the
    events worth showing (reports, revocations, warnings)."""
    if kind == "register":
        vid, key, vetted = payload
        r = client.post("/devices", json={"device_id": vid, "public_key": key, "vetted": vetted}, headers=ADMIN)
        assert r.status_code == 201, r.text
    elif kind == "report":
        r = client.post("/reports", content=payload.encode(), headers={"Content-Type": "application/json"})
        env = json.loads(payload)
        rec = {"t": t_rel, "kind": "report", "device": env["sender"], "event_id": env["msg_id"],
               "type": env["event"].get("type"), "http": r.status_code}
        if r.status_code == 201:
            results[r.json()["status"]] += 1
            rec["result"] = r.json()["status"]
        else:
            reason = r.json()["detail"].split(":")[0]
            results["rejected"][reason] = results["rejected"].get(reason, 0) + 1
            rec["result"] = "rejected: " + r.json()["detail"]
        return rec
    elif kind == "lost":
        results["lost"] += 1
        return {"t": t_rel, "kind": "lost", "device": payload, "result": "report lost on the BLE link"}
    elif kind == "beacons":
        client.post("/beacons", json=payload)
    elif kind == "revoke":
        client.post(f"/devices/{payload}/revoke", headers=ADMIN)
        return {"t": t_rel, "kind": "revoke", "device": payload, "result": "device revoked by the administrator"}
    elif kind == "settle":
        upd = client.post("/admin/settle", headers=ADMIN).json()["updates"]
        if upd:
            return {"t": t_rel, "kind": "settle", "result": f"reputation updated ({len(upd)} changes)",
                    "details": upd}
    elif kind == "query":
        car, i = payload
        typ, pos, lane = (HAZARDS + run.fakes)[i]
        lat, lon = position(car.direction, car.travelled(pos) - WARN_CHECK_M)
        hz = client.get("/hazards", params={"lat": lat, "lon": lon, "radius_m": WARN_CHECK_M + 50,
                                            "heading_deg": 90 if car.direction == "E" else 270}).json()
        hlat, hlon = position("E", pos)
        match = [h for h in hz if h["type"] == typ and _dist(h, hlat, hlon) <= MATCH_M]
        real_pos = [(rt, position("E", rp)) for rt, rp, rl in HAZARDS if rl == car.direction]
        false_alert = [h for h in hz if not any(h["type"] == rt and _dist(h, *rp) <= MATCH_M for rt, rp in real_pos)]
        false_warn = [h for h in false_alert if h["level"] == "warning"]
        level = "warning" if any(h["level"] == "warning" for h in match) else (
            "advisory" if match else "none")
        q = {"t": t_rel, "car": car.vid, "hazard": i, "fake": i >= len(HAZARDS), "level": level,
             "false_warnings": len(false_warn), "false_alerts": len(false_alert)}
        queries.append(q)
        return {"t": t_rel, "kind": "query", "device": car.vid, "type": typ, "fake": q["fake"], "level": level,
                "result": f"{WARN_CHECK_M:.0f} m before {'an invented' if q['fake'] else 'a real'} {typ}: {level}"}
    return None


def simulate(scn: Scenario, seed: int, camera: str = "assumed") -> dict:
    clock = [UTC0]
    store = Store()
    client = TestClient(create_app(store, lambda: clock[0], admin_token="sil"))
    run = prepare(scn, seed, camera=camera)
    results, queries = new_results(), []
    for t_rel, kind, payload in run.events:
        clock[0] = UTC0 + t_rel
        apply(client, run, t_rel, kind, payload, results, queries)
    return summarise(scn, run.honest, run.attackers, results, queries, device_delays(run), store)


def device_delays(run: Run) -> list[float]:
    """Impact (detected_at) to device emission, from the device code itself."""
    out = []
    for car in run.honest:
        for t_emit, line in run.emitted[car.vid]:
            ev = json.loads(line)["event"]
            out.append(t_emit - (from_iso(ev["detected_at"]) - run.utc0))
    return out


def _dist(h: dict, lat: float, lon: float) -> float:
    from icvsp.geo import distance_m
    return distance_m(h["latitude"], h["longitude"], lat, lon)


def summarise(scn, honest, attackers, results, queries, lat_dev, store) -> dict:
    real = [q for q in queries if not q["fake"]]
    fake = [q for q in queries if q["fake"]]
    late = [q for q in real if q["t"] > 600]   # after the first 10 minutes (enough cars have passed)

    def share(qs, level):
        return sum(q["level"] == level for q in qs) / len(qs) if qs else float("nan")

    lat_dev.sort()
    p = lambda a, x: a[min(len(a) - 1, int(x * len(a)))] if a else float("nan")  # noqa: E731
    reps = store.reputations()
    return {
        "scenario": scn.name, "title": scn.title, "cars": scn.cars, "minutes": scn.minutes,
        "reports": results, "queries_real": len(real), "queries_fake": len(fake),
        "warned_real": share(real, "warning"), "advisory_real": share(real, "advisory"),
        "warned_real_after_10min": share(late, "warning"),
        "warned_fake": share(fake, "warning"), "advisory_fake": share(fake, "advisory"),
        "queries_with_false_warning": sum(q["false_warnings"] > 0 for q in queries) / len(queries),
        "queries_with_false_advisory_or_warning": sum(q["false_alerts"] > 0 for q in queries) / len(queries),
        "device_delay_median_s": p(lat_dev, 0.5), "device_delay_p95_s": p(lat_dev, 0.95),
        "reliability_honest_mean": sum((g + 1) / (g + b + 4) for v, (g, b) in reps.items()
                                       if v.startswith("car-")) / max(1, len(honest)),
    }


def report_md(rows: list[dict], seed: int) -> str:
    out = ["# System simulation (software in the loop)", "",
           f"Generated by `sil/run_sil.py --seed {seed}`. Each car runs the real device code; reports go through "
           "the backend API and the trust engine. Sensor hit rates, radio and network behaviour are assumptions "
           "(see the top of `run_sil.py`), so these numbers describe the system logic under those assumptions, "
           "not road performance.", "",
           "*Warned* = a car approaching a real hazard got a warning-level hazard from `GET /hazards` "
           f"{WARN_CHECK_M:.0f} m before it. *Advisory* = it got only an advisory. *Fake warned* = a car got a warning "
           "for a hazard an attacker invented. *Any false warning shown* = share of all hazard queries that returned a "
           "warning for something that is not a real hazard (invented ones and honest devices' false reports).", "",
           "| Scenario | Real hazards: warned | advisory only | warned, after first 10 min | Fake hazards: warned "
           "| Any false warning shown | Reports accepted / duplicate / rejected / lost | Device delay median / p95 (s) "
           "| Mean honest reliability |",
           "|---|---|---|---|---|---|---|---|---|"]
    pct = lambda x: "–" if x != x else f"{100 * x:.0f} %"  # noqa: E731
    for r in rows:
        rep = r["reports"]
        rej = sum(rep["rejected"].values())
        out.append(f"| {r['title']} | {pct(r['warned_real'])} | {pct(r['advisory_real'])} | "
                   f"{pct(r['warned_real_after_10min'])} | {pct(r['warned_fake'])} | "
                   f"{pct(r['queries_with_false_warning'])} | "
                   f"{rep['accepted']} / {rep['duplicate']} / {rej} / {rep['lost']} | "
                   f"{r['device_delay_median_s']:.1f} / {r['device_delay_p95_s']:.1f} | "
                   f"{r['reliability_honest_mean']:.2f} |")
    out += ["", "Rejected reports by reason:", ""]
    for r in rows:
        if r["reports"]["rejected"]:
            out.append(f"- {r['title']}: " + ", ".join(f"{k} ({v})" for k, v in r["reports"]["rejected"].items()))
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    ap.add_argument("--cars", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "sil" / "results"))
    ap.add_argument("--camera", choices=list(CAMERAS), default="assumed",
                    help="camera model profile (hit and false-report rates measured on held-out video)")
    a = ap.parse_args()
    build()
    names = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    rows = []
    for n in names:
        scn = SCENARIOS[n]
        if a.cars:
            scn.cars = a.cars
        print(f"running {n} ...", flush=True)
        rows.append(simulate(scn, a.seed, a.camera))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(rows, indent=1))
    md = report_md(rows, a.seed)
    (out / "summary.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
