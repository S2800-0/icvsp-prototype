"""Schema validation and plausibility checks.

Plausibility checks are the "data-centric" half of misbehaviour detection
(van der Heijden et al., 2019): they judge whether a single report is
internally consistent and consistent with what the sender itself broadcast,
without needing any other vehicle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .geo import distance_m
from .model import SCHEMA_VERSION, Beacon, SafetyEvent, from_iso

HAZARD_TYPES = ("pothole", "speed_bump")
SEVERITIES = ("low", "medium", "high")


def schema_errors(d: dict) -> list[str]:
    """Hand-written mirror of schema/safety_event.schema.json (keeps the engine dependency-free)."""
    errs: list[str] = []
    allowed = {"schema_version", "event_id", "device_id", "boot_id", "sequence", "type", "detected_at", "gps_valid",
               "latitude", "longitude", "position_accuracy_m", "road_segment", "speed_kmh", "heading_deg",
               "severity", "detection_confidence", "imu_peak_g", "model"}
    required = allowed - {"road_segment", "imu_peak_g", "model"}
    if not isinstance(d, dict):
        return ["event is not an object"]
    for k in sorted(required - d.keys()):
        errs.append(f"missing field '{k}'")
    for k in sorted(d.keys() - allowed):
        errs.append(f"unexpected field '{k}'")
    if errs:
        return errs

    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def integer(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if d["schema_version"] != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}")
    for k in ("event_id", "device_id"):
        if not isinstance(d[k], str) or not d[k]:
            errs.append(f"{k} must be a non-empty string")
    if not (integer(d["boot_id"]) and d["boot_id"] >= 0):
        errs.append("boot_id must be an integer ≥ 0")
    if not (integer(d["sequence"]) and d["sequence"] >= 1):
        errs.append("sequence must be an integer ≥ 1")
    if d["type"] not in HAZARD_TYPES:
        errs.append(f"type must be one of {HAZARD_TYPES}")
    try:
        from_iso(d["detected_at"])
    except (TypeError, ValueError, AttributeError):
        errs.append("detected_at must be an ISO 8601 date-time")
    if not isinstance(d["gps_valid"], bool):
        errs.append("gps_valid must be true or false")
    elif d["gps_valid"]:
        if not (num(d["latitude"]) and -90 <= d["latitude"] <= 90):
            errs.append("latitude out of range")
        if not (num(d["longitude"]) and -180 <= d["longitude"] <= 180):
            errs.append("longitude out of range")
        if not (num(d["position_accuracy_m"]) and d["position_accuracy_m"] > 0):
            errs.append("position_accuracy_m must be > 0")
        if not (num(d["speed_kmh"]) and d["speed_kmh"] >= 0):
            errs.append("speed_kmh must be ≥ 0")
        if not (num(d["heading_deg"]) and 0 <= d["heading_deg"] < 360):
            errs.append("heading_deg must be in [0, 360)")
    else:
        for k in ("latitude", "longitude", "position_accuracy_m"):
            if d[k] is not None:
                errs.append(f"{k} must be null when gps_valid is false (no invented position)")
    if "road_segment" in d and (not isinstance(d["road_segment"], str) or not d["road_segment"]):
        errs.append("road_segment must be a non-empty string")
    if d["severity"] not in SEVERITIES:
        errs.append(f"severity must be one of {SEVERITIES}")
    if not (num(d["detection_confidence"]) and 0 <= d["detection_confidence"] <= 1):
        errs.append("detection_confidence must be in [0, 1]")
    if "imu_peak_g" in d and not (num(d["imu_peak_g"]) and d["imu_peak_g"] >= 0):
        errs.append("imu_peak_g must be a number ≥ 0")
    if "model" in d and not isinstance(d["model"], str):
        errs.append("model must be a string")
    return errs


@dataclass
class Check:
    """Outcome of the plausibility checks for one event.

    verdict: 'accept', 'downgrade' (kept, but with lower consistency) or 'reject'.
    consistency: the g factor used in the trust weight (0–1).
    """
    verdict: str = "accept"
    consistency: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def reject(self, why: str) -> "Check":
        self.verdict, self.consistency = "reject", 0.0
        self.reasons.append(why)
        return self

    def downgrade(self, factor: float, why: str) -> None:
        if self.verdict != "reject":
            self.verdict = "downgrade"
            self.consistency *= factor
            self.reasons.append(why)


def trace_consistency(trace: list[Beacon], cfg: Config) -> tuple[float, str | None]:
    """Kinematic check on a vehicle's own beacons: does it ever 'teleport'?"""
    worst = 0.0
    for a, b in zip(trace, trace[1:]):
        dt = b.t - a.t
        if dt <= 0:
            continue
        worst = max(worst, distance_m(a.lat, a.lon, b.lat, b.lon) / dt)
    if worst > cfg.max_trace_speed_mps:
        return 0.4, f"position trace jumps (implied {worst * 3.6:.0f} km/h)"
    return 1.0, None


def plausibility(ev: SafetyEvent, sender_trace: list[Beacon], now: float, cfg: Config) -> Check:
    chk = Check()

    # 1. time
    if ev.t > now + cfg.max_future_s:
        return chk.reject(f"timestamp is {ev.t - now:.0f} s in the future")
    max_age = cfg.max_age_s.get(ev.type, 24 * 3600)
    if now - ev.t > max_age:
        return chk.reject(f"older than {max_age / 3600:.0f} h (stale, possible replay)")

    # 2. location: a report without a position fix is kept for the log but cannot be placed on a road
    if not ev.gps_valid:
        return chk.reject("no valid GPS position (kept for the log, not used for warnings)")
    if ev.acc_m > cfg.max_acc_m:
        return chk.reject(f"GPS accuracy {ev.acc_m:.0f} m is too poor to place on a road segment")
    if ev.acc_m > cfg.good_acc_m:
        f = 1 - 0.8 * (ev.acc_m - cfg.good_acc_m) / (cfg.max_acc_m - cfg.good_acc_m)
        chk.downgrade(f, f"GPS accuracy {ev.acc_m:.0f} m")

    # 3. reported speed
    if ev.speed_kmh > cfg.max_speed_kmh:
        return chk.reject(f"reported speed {ev.speed_kmh:.0f} km/h is implausible")

    # 4. does the report agree with where the sender itself said it was?
    near = [b for b in sender_trace if abs(b.t - ev.t) <= cfg.sender_match_dt_s]
    if not near:
        chk.downgrade(0.6, "no position beacons from the sender around the report time")
    else:
        b = min(near, key=lambda b: abs(b.t - ev.t))
        gap = distance_m(ev.lat, ev.lon, b.lat, b.lon)
        if gap > cfg.sender_max_offset_m:
            chk.downgrade(0.3, f"report location is {gap:.0f} m from the sender's own position "
                               f"(inconsistent with sender's own position)")

    # 5. does the sender's trace itself look physically possible?
    k, why = trace_consistency(sender_trace, cfg)
    if why:
        chk.downgrade(k, why)
    return chk
