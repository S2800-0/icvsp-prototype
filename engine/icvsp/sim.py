"""A deliberately small road simulator.

The engine only needs to know who drove where, when, and what they reported,
so a straight road segment with vehicles at constant speed is enough to
exercise it. The simulator's only output is beacons + Safety Events, written
in the trace format in tracefile.py, so it can later be swapped for SUMO
(floating car data) without touching the engine.

Vehicle roles
  honest        reports the hazard if it is real, stays silent otherwise
  miss          drives over the spot with the detector on but reports nothing
  false_report  reports a hazard at `claimed_s` whether or not it exists
  sybil         one attacker broadcasting as 1 + `clones` identities on one trajectory
  spoof         reports a hazard at `claimed_s` it never drove near
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .geo import offset
from .model import Beacon, HazardState, SafetyEvent, from_iso

BASE_T = from_iso("2026-11-04T08:40:00Z")
LANE_M = 1.75


@dataclass(frozen=True)
class Segment:
    id: str = "seg_1142"
    lat0: float = 30.0444          # start point (a street in Cairo, for realistic coordinates)
    lon0: float = 31.2357
    heading_deg: float = 90.0      # eastbound
    length_m: float = 1000.0

    def point(self, s: float, across: float = 0.0) -> tuple[float, float]:
        """lat/lon at distance s along the segment, `across` metres to the right."""
        h = math.radians(self.heading_deg)
        north = s * math.cos(h) - across * math.sin(h)
        east = s * math.sin(h) + across * math.cos(h)
        return offset(self.lat0, self.lon0, north, east)


@dataclass
class VehicleSpec:
    vid: str
    t_start: float                 # seconds after scenario start when it appears
    s0: float                      # starting position along the segment (m)
    speed_mps: float = 16.7        # about 60 km/h
    direction: int = +1            # +1 along the segment heading, -1 opposite lane
    role: str = "honest"
    confidence: float = 0.9
    acc_m: float = 4.0
    history: tuple[float, float] | None = (20, 2)   # past (good, bad) outcomes; None = brand-new identity
    clones: int = 0                # sybil only
    detector_on: bool = True


@dataclass
class Scenario:
    key: str
    title: str
    description: str
    vehicles: list[VehicleSpec]
    hazard_s: float | None = 600.0     # where the real pothole is; None = there isn't one
    hazard_direction: int = +1         # the lane it is in (+1 along the segment heading)
    claimed_s: float = 600.0           # where false/spoofed/Sybil reports put it
    duration_s: float = 40.0
    segment: Segment = field(default_factory=Segment)
    gps_sigma_m: float = 1.0
    packet_loss: float = 0.0
    cloud_online: bool = True
    replay_age_s: float | None = None  # replay an old report from vehicle A this many seconds old
    observer: str = "D"                # the vehicle the audience watches ("does D get warned?")


@dataclass
class SimOutput:
    beacons: list[Beacon]
    events: list[SafetyEvent]
    histories: dict[str, tuple[float, float]]
    now: float


def run(scn: Scenario, seed: int = 7) -> SimOutput:
    rng = random.Random(seed)
    seg = scn.segment
    beacons: list[Beacon] = []
    events: list[SafetyEvent] = []
    histories: dict[str, tuple[float, float]] = {}
    counter = [0]

    def noisy(lat: float, lon: float, sigma: float) -> tuple[float, float]:
        return offset(lat, lon, rng.gauss(0, sigma), rng.gauss(0, sigma))

    def emit(vid: str, v: VehicleSpec, t: float, s_report: float) -> None:
        counter[0] += 1
        across = LANE_M * v.direction
        lat, lon = noisy(*seg.point(s_report, across), v.acc_m / 2)
        events.append(SafetyEvent(
            event_id=f"evt_{counter[0]:04d}_{vid}", type="pothole", t=BASE_T + t, lat=lat, lon=lon,
            acc_m=v.acc_m, road_segment=seg.id, speed_kmh=round(v.speed_mps * 3.6, 1),
            heading_deg=_heading(seg, v.direction), severity="high", confidence=v.confidence,
            source=vid, model="pothole-det v0.x"))

    for v in scn.vehicles:
        ids = [v.vid] if v.role != "sybil" else [f"{v.vid}{k + 1}" for k in range(v.clones + 1)]
        for vid in ids:
            if v.history is not None:
                histories[vid] = v.history

        # trajectory (1 Hz beacons), shared by all identities of a Sybil attacker
        t = v.t_start
        path: list[tuple[float, float]] = []
        while t <= scn.duration_s:
            s = v.s0 + v.direction * v.speed_mps * (t - v.t_start)
            if 0 <= s <= seg.length_m:
                path.append((t, s))
            t += 1.0
        for vid in ids:
            jitter = 0.3 if v.role == "sybil" else scn.gps_sigma_m
            for t, s in path:
                lat, lon = noisy(*seg.point(s, LANE_M * v.direction), jitter)
                beacons.append(Beacon(vid, BASE_T + t, lat, lon, v.speed_mps, _heading(seg, v.direction), v.detector_on))

        # reports
        if v.role == "honest":
            # an honest detector only sees a pothole in its own lane
            target = scn.hazard_s if v.direction == scn.hazard_direction else None
        else:
            target = scn.claimed_s
        if v.role in ("honest", "false_report", "sybil") and target is not None:
            t_cross = _crossing_time(v, target)
            if t_cross is not None and t_cross <= scn.duration_s:
                for vid in ids:
                    emit(vid, v, t_cross + 0.4, target)
        elif v.role == "spoof":
            emit(v.vid, v, v.t_start + 10, scn.claimed_s)

    if scn.replay_age_s is not None:
        # an attacker re-broadcasts vehicle A's genuine report from long ago
        lat, lon = seg.point(scn.claimed_s, LANE_M)
        events.append(SafetyEvent(
            event_id="evt_0917_A", type="pothole", t=BASE_T - scn.replay_age_s, lat=lat, lon=lon, acc_m=4.0,
            road_segment=seg.id, speed_kmh=60.0, heading_deg=seg.heading_deg, severity="high",
            confidence=0.9, source="A", model="pothole-det v0.x"))
        histories.setdefault("A", (20, 2))

    if scn.packet_loss > 0:
        beacons = [b for b in beacons if rng.random() >= scn.packet_loss]
        events = [e for e in events if rng.random() >= scn.packet_loss]
    return SimOutput(beacons, events, histories, BASE_T + scn.duration_s)


def _heading(seg: Segment, direction: int) -> float:
    return seg.heading_deg % 360 if direction > 0 else (seg.heading_deg + 180) % 360


def _crossing_time(v: VehicleSpec, s_target: float) -> float | None:
    dist = (s_target - v.s0) * v.direction
    if dist < 0 or v.speed_mps <= 0:
        return None
    return v.t_start + dist / v.speed_mps


# ---------------------------------------------------------------------------
# The scripted scenarios from the proposal (Appendix B) and the deck (slide 10),
# plus two extra attacks for the security side.
# ---------------------------------------------------------------------------
def _approaching_d() -> VehicleSpec:
    return VehicleSpec("D", t_start=25, s0=0)


SCENARIOS: dict[str, Scenario] = {s.key: s for s in [
    Scenario(
        "single", "Single report",
        "Vehicle A detects a pothole and is the only source. Vehicle D is approaching on the same segment.",
        [VehicleSpec("A", 0, 400, confidence=0.91), _approaching_d()]),
    Scenario(
        "duplicates", "Duplicates merge",
        "A, B and C report the same pothole seconds apart. F passes in the opposite lane.",
        [VehicleSpec("A", 0, 400, confidence=0.91), VehicleSpec("B", 0, 300, confidence=0.86),
         VehicleSpec("C", 0, 200, confidence=0.88), _approaching_d(),
         VehicleSpec("F", 20, 900, direction=-1)]),
    Scenario(
        "false_report", "False report",
        "A reports a pothole that isn't there. B and C drive over the same spot and report nothing.",
        [VehicleSpec("A", 0, 400, role="false_report", confidence=0.82, history=(5, 4)),
         VehicleSpec("B", 0, 350), VehicleSpec("C", 0, 250), _approaching_d()],
        hazard_s=None),
    Scenario(
        "sybil", "Sybil attack",
        "One attacker broadcasts as five new identities (S1–S5) on one trajectory. Honest B drives over the spot.",
        [VehicleSpec("S", 0, 400, role="sybil", clones=4, confidence=0.95, history=None),
         VehicleSpec("B", 0, 300), _approaching_d()],
        hazard_s=None),
    Scenario(
        "cloud_outage", "Cloud outage",
        "Same traffic as the duplicates scenario, but AWS is unreachable.",
        [VehicleSpec("A", 0, 400, confidence=0.91), VehicleSpec("B", 0, 300, confidence=0.86),
         VehicleSpec("C", 0, 200, confidence=0.88), _approaching_d(),
         VehicleSpec("F", 20, 900, direction=-1)],
        cloud_online=False),
    Scenario(
        "replay", "Replay attack",
        "An attacker re-broadcasts A's genuine pothole report from three days ago. The pothole has since been fixed.",
        [VehicleSpec("B", 0, 300), _approaching_d()],
        hazard_s=None, replay_age_s=3 * 24 * 3600),
    Scenario(
        "gps_spoof", "GPS spoofing",
        "New vehicle M reports a pothole at a place its own position beacons show it never reached.",
        [VehicleSpec("M", 0, 0, speed_mps=8.0, role="spoof", history=None), _approaching_d()],
        hazard_s=None),
]}

EXPECTED: dict[str, HazardState | None] = {
    "single": HazardState.UNCONFIRMED,
    "duplicates": HazardState.CONFIRMED,
    "false_report": HazardState.SUPPRESSED,
    "sybil": HazardState.SUPPRESSED,
    "cloud_outage": HazardState.CONFIRMED,
    "replay": None,            # the replayed event is rejected, so no hazard at all
    "gps_spoof": HazardState.SUPPRESSED,
}


def random_scenario(rng: random.Random, n_vehicles: int = 10, malicious_frac: float = 0.2,
                    hazard_real: bool | None = None, tpr: float = 0.85, fpr: float = 0.05,
                    packet_loss: float = 0.0, gps_sigma_m: float = 1.0,
                    attacker_history: tuple[float, float] | None = None) -> Scenario:
    """Randomised traffic for large experiment runs (Stage 2).

    Honest vehicles detect a real hazard with probability `tpr` and invent one
    with probability `fpr`. These rates are assumptions until the pothole
    detector has measured values.

    Attackers are brand-new identities by default (attacker_history=None).
    Pass e.g. (20, 2) to model attackers with an established good history,
    which removes the reputation advantage and is the harder case.
    When the hazard is real, attackers stay silent (hiding it); when it is not,
    they fabricate it by false report, Sybil identities or position spoofing.
    """
    real = rng.random() < 0.5 if hazard_real is None else hazard_real
    vehicles: list[VehicleSpec] = []
    for i in range(n_vehicles):
        spec = VehicleSpec(f"V{i:02d}", t_start=rng.uniform(0, 20), s0=rng.uniform(0, 500),
                           speed_mps=rng.uniform(11, 22), confidence=round(rng.uniform(0.75, 0.97), 2),
                           history=(rng.randint(5, 30), rng.randint(0, 5)),
                           # reported accuracy ≈ 2σ; the report position itself is drawn with σ = acc/2
                           acc_m=max(4.0, 2 * gps_sigma_m))
        if rng.random() < malicious_frac:
            spec.role = rng.choice(["false_report", "sybil", "spoof"]) if not real else "miss"
            spec.history = attacker_history
            if spec.role == "sybil":
                spec.clones = rng.randint(2, 6)
        else:
            hit = rng.random() < (tpr if real else fpr)
            spec.role = "honest" if (real and hit) else ("false_report" if hit else "miss")
        vehicles.append(spec)
    return Scenario("random", "Random", f"{n_vehicles} vehicles, {malicious_frac:.0%} malicious, "
                    f"hazard {'real' if real else 'absent'}", vehicles,
                    hazard_s=600.0 if real else None, duration_s=70.0,
                    gps_sigma_m=gps_sigma_m, packet_loss=packet_loss)
