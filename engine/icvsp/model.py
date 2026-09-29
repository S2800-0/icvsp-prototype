"""Data types shared by the engine, the simulator and the trace files."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


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
    """One hazard observation, as it leaves the vehicle (see schema/safety_event.schema.json)."""
    event_id: str
    type: str
    t: float
    lat: float
    lon: float
    acc_m: float
    road_segment: str
    speed_kmh: float
    heading_deg: float
    severity: str
    confidence: float
    source: str
    model: str = "sim"

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "timestamp": to_iso(self.t),
            "location": {"lat": self.lat, "lon": self.lon, "acc_m": self.acc_m},
            "road_segment": self.road_segment,
            "speed_kmh": self.speed_kmh,
            "heading": self.heading_deg,
            "severity": self.severity,
            "confidence": self.confidence,
            "source": self.source,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SafetyEvent":
        loc = d["location"]
        return cls(
            event_id=d["event_id"], type=d["type"], t=from_iso(d["timestamp"]),
            lat=loc["lat"], lon=loc["lon"], acc_m=loc["acc_m"],
            road_segment=d["road_segment"], speed_kmh=d["speed_kmh"], heading_deg=d["heading"],
            severity=d["severity"], confidence=d["confidence"], source=d["source"],
            model=d.get("model", "unknown"),
        )


class HazardState(str, Enum):
    SUPPRESSED = "suppressed"
    UNCONFIRMED = "unconfirmed"
    CORROBORATED = "corroborated"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
