"""Data types shared by the engine, the simulator and the trace files."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

SCHEMA_VERSION = 2


def to_iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


@dataclass(frozen=True)
class Beacon:
    """A periodic position message from a vehicle (one row of a trace file).

    detector_on says whether the vehicle's hazard detector was running, which is
    what lets a pass-by with no report count as negative evidence.
    """
    vehicle_id: str
    t: float
    lat: float
    lon: float
    speed_mps: float
    heading_deg: float
    detector_on: bool = True


@dataclass(frozen=True)
class SafetyEvent:
    """One hazard observation, as it leaves the vehicle (see schema/safety_event.schema.json, version 2).

    Inside the engine the names are short (t, lat, lon, acc_m, source, confidence); to_dict / from_dict map
    them to the message field names.
    """
    event_id: str
    type: str
    t: float
    lat: float | None
    lon: float | None
    acc_m: float | None
    road_segment: str
    speed_kmh: float | None
    heading_deg: float | None
    severity: str
    confidence: float
    source: str
    model: str = "sim"
    boot_id: int = 0
    sequence: int = 1
    gps_valid: bool = True
    imu_peak_g: float | None = None

    def to_dict(self) -> dict:
        d = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "device_id": self.source,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "type": self.type,
            "detected_at": to_iso(self.t),
            "gps_valid": self.gps_valid,
            "latitude": self.lat,
            "longitude": self.lon,
            "position_accuracy_m": self.acc_m,
            "road_segment": self.road_segment,
            "speed_kmh": self.speed_kmh,
            "heading_deg": self.heading_deg,
            "severity": self.severity,
            "detection_confidence": self.confidence,
            "model": self.model,
        }
        if self.imu_peak_g is not None:
            d["imu_peak_g"] = self.imu_peak_g
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SafetyEvent":
        return cls(
            event_id=d["event_id"], type=d["type"], t=from_iso(d["detected_at"]),
            lat=d["latitude"], lon=d["longitude"], acc_m=d["position_accuracy_m"],
            road_segment=d.get("road_segment", ""), speed_kmh=d["speed_kmh"], heading_deg=d["heading_deg"],
            severity=d["severity"], confidence=d["detection_confidence"], source=d["device_id"],
            model=d.get("model", "unknown"), boot_id=d["boot_id"], sequence=d["sequence"],
            gps_valid=d["gps_valid"], imu_peak_g=d.get("imu_peak_g"),
        )


class HazardState(str, Enum):
    SUPPRESSED = "suppressed"
    UNCONFIRMED = "unconfirmed"
    CORROBORATED = "corroborated"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
