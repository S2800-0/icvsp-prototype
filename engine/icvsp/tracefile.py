"""The swappable trace format.

  traces.csv    one row per position beacon:
                vehicle_id,t,lat,lon,speed_mps,heading_deg,detector_on
  events.jsonl  one Safety Event message per line (schema/safety_event.schema.json)

Anything that can produce these two files can drive the engine: the built-in
simulator today, SUMO later (its floating-car-data output has id, time,
position, speed and angle per vehicle per step), or a real test drive.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .model import Beacon, SafetyEvent

FIELDS = ["vehicle_id", "t", "lat", "lon", "speed_mps", "heading_deg", "detector_on"]


def write(folder: str | Path, beacons: list[Beacon], events: list[SafetyEvent]) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "traces.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for b in beacons:
            w.writerow([b.vehicle_id, f"{b.t:.3f}", f"{b.lat:.7f}", f"{b.lon:.7f}",
                        f"{b.speed_mps:.2f}", f"{b.heading_deg:.1f}", int(b.detector_on)])
    with open(folder / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e.to_dict()) + "\n")


def read(folder: str | Path) -> tuple[list[Beacon], list[dict]]:
    """Returns beacons and the raw event dicts (raw so the engine can schema-check them)."""
    folder = Path(folder)
    beacons: list[Beacon] = []
    with open(folder / "traces.csv", newline="") as f:
        for r in csv.DictReader(f):
            beacons.append(Beacon(r["vehicle_id"], float(r["t"]), float(r["lat"]), float(r["lon"]),
                                  float(r["speed_mps"]), float(r["heading_deg"]), r["detector_on"] == "1"))
    events: list[dict] = []
    with open(folder / "events.jsonl") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return beacons, events
