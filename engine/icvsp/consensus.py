"""The decision engine: Safety Events + position beacons in, hazard decisions out.

Steps inside Engine.decide():
  1. replay protection and plausibility checks on every event
  2. trust weight for every surviving event
  3. merge reports of the same hazard (type, place, direction)
  4. find negative evidence: vehicles that drove over the spot, detector on, and said nothing (unless they
     braked there and sped up again: that supports the hazard instead, see Config.fleet_slowing)
  5. independence check: identities that move as one are counted once (Sybil defence)
  6. net evidence → hazard state, with a written reason for every step
"""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config
from .geo import EARTH_R, along_and_across, distance_m, heading_diff
from .model import Beacon, HazardState, SafetyEvent
from .trust import Reputation, Trust, freshness
from .validation import Check, plausibility, schema_errors, trace_consistency


@dataclass
class Scored:
    event: SafetyEvent
    check: Check
    trust: Trust


@dataclass
class Evidence:
    source: str
    kind: str                 # "support" or "contra"
    trust: Trust
    event_id: str | None = None
    group: int | None = None  # independence group index; members of a group count once

    @property
    def weight(self) -> float:
        return self.trust.weight


@dataclass
class Rejected:
    event_id: str
    source: str
    reasons: list[str]


@dataclass
class Decision:
    hazard_id: str
    type: str
    lat: float
    lon: float
    heading_deg: float
    road_segment: str
    first_seen: float
    state: HazardState
    net: float
    independent_sources: int
    support: list[Evidence]
    contra: list[Evidence]
    groups: list[list[str]]           # groups of identities that were collapsed into one
    reasons: list[str]
    sybil_suspected: bool = False
    severity: str = "unknown"         # largest measured severity among the reports, or "unknown" (camera only)
    slowing: list[Evidence] = field(default_factory=list)  # vehicles that braked at the spot (supporting only)

    @property
    def action(self) -> str:
        return {
            HazardState.SUPPRESSED: "No warning. Logged for review.",
            HazardState.UNCONFIRMED: "Low-impact advisory only (single source).",
            HazardState.CORROBORATED: "Warning to approaching vehicles.",
            HazardState.CONFIRMED: "Warning to approaching vehicles; road twin updated.",
            HazardState.EXPIRED: "No warning. Evidence has aged out.",
        }[self.state]


@dataclass
class Report:
    now: float
    decisions: list[Decision] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)


class Engine:
    def __init__(self, cfg: Config | None = None, reputation: Reputation | None = None):
        self.cfg = cfg or Config()
        self.rep = reputation or Reputation(self.cfg.prior_good, self.cfg.prior_bad)
        self.traces: dict[str, list[Beacon]] = defaultdict(list)
        self.inbox: list[SafetyEvent] = []
        self.malformed: list[Rejected] = []

    # ---------- input ----------
    def ingest_beacon(self, b: Beacon) -> None:
        self.traces[b.vehicle_id].append(b)

    def ingest_event(self, ev: SafetyEvent) -> None:
        self.inbox.append(ev)

    def ingest_dict(self, d: dict) -> bool:
        """Accept a raw Safety Event message; malformed messages are rejected before anything else."""
        errs = schema_errors(d)
        if errs:
            self.malformed.append(Rejected(str(d.get("event_id", "?")), str(d.get("device_id", "?")),
                                           ["schema: " + e for e in errs]))
            return False
        self.inbox.append(SafetyEvent.from_dict(d))
        return True

    # ---------- decision ----------
    def decide(self, now: float) -> Report:
        cfg = self.cfg
        for tr in self.traces.values():
            tr.sort(key=lambda b: b.t)
        self._reach = {vid: trace_reach(tr, cfg) for vid, tr in self.traces.items()}
        self._order = {vid: i for i, vid in enumerate(self.traces)}
        self._index(cfg)
        report = Report(now=now, rejected=list(self.malformed))

        accepted: list[Scored] = []
        seen_ids: set[str] = set()
        seen_seq: set[tuple[str, int, int]] = set()
        for ev in sorted(self.inbox, key=lambda e: e.t):
            if ev.event_id in seen_ids:
                report.rejected.append(Rejected(ev.event_id, ev.source, ["duplicate event_id (replay protection)"]))
                continue
            if (ev.source, ev.boot_id, ev.sequence) in seen_seq:
                report.rejected.append(Rejected(ev.event_id, ev.source,
                                                [f"repeated boot {ev.boot_id} / sequence {ev.sequence} from this device"]))
                continue
            seen_ids.add(ev.event_id)
            seen_seq.add((ev.source, ev.boot_id, ev.sequence))
            chk = plausibility(ev, self.traces.get(ev.source, []), now, cfg)
            if chk.verdict == "reject":
                report.rejected.append(Rejected(ev.event_id, ev.source, chk.reasons))
                continue
            tr = Trust(c=ev.confidence, g=chk.consistency, h=self.rep.score(ev.source),
                       f=freshness(now - ev.t, ev.type, cfg))
            accepted.append(Scored(ev, chk, tr))

        for i, cluster in enumerate(self._cluster(accepted)):
            report.decisions.append(self._decide_cluster(f"hz_{i + 1:04d}", cluster, now))
        return report

    def _index(self, cfg: Config) -> None:
        """Grid indexes so each hazard only looks at reports and vehicles near it. Anything that can reach further
        than GRID_REACH_M (poor GPS accuracy, long gaps between positions) is always checked, so no result changes."""
        pts = [e.lat for e in self.inbox if e.gps_valid] + [b.lat for tr in self.traces.values() for b in tr[:1]]
        lat0 = sum(pts) / len(pts) if pts else 0.0
        self._event_grid, self._vehicle_grid = _Grid(lat0), _Grid(lat0)
        self._segments: dict[str, dict[tuple[int, int], list[int]]] = {}
        for e in self.inbox:
            if not e.gps_valid:
                continue
            if 3 * cfg.merge_radius_m + e.acc_m <= GRID_REACH_M:
                self._event_grid.add(e.lat, e.lon, e)
            else:
                self._event_grid.always.append(e)
        for vid, tr in self.traces.items():
            reach = self._reach.get(vid)
            if reach is None:
                continue
            pad_m = (reach[1] - max(b.lat for b in tr)) * 111320.0       # step + pass radius + 1 m (trace_reach)
            if pad_m <= GRID_REACH_M:
                segs: dict[tuple[int, int], list[int]] = defaultdict(list)   # cell -> steps with an end in it
                for k, (a, b) in enumerate(zip(tr, tr[1:])):
                    ka, kb = self._vehicle_grid.key(a.lat, a.lon), self._vehicle_grid.key(b.lat, b.lon)
                    segs[ka].append(k)
                    if kb != ka:
                        segs[kb].append(k)
                self._segments[vid] = segs
                for key in segs:
                    self._vehicle_grid.cells[key].append(vid)
            else:
                self._vehicle_grid.always.append(vid)

    def _near_steps(self, vid: str, lat: float, lon: float) -> list[int] | None:
        """Indexes (in time order) of the steps of a vehicle's trace with an end in the 3 x 3 cells around a point,
        or None to check every step. A crossing step has an end within (step + pass radius) of the point, which is
        inside those cells for every vehicle indexed by cell (see _index)."""
        segs = self._segments.get(vid)
        if segs is None:
            return None
        i, j = self._vehicle_grid.key(lat, lon)
        return sorted({k for di in (-1, 0, 1) for dj in (-1, 0, 1) for k in segs.get((i + di, j + dj), ())})

    def _cluster(self, items: list[Scored]) -> list[list[Scored]]:
        """Each report joins the FIRST existing cluster (in creation order) of its type and direction whose centre is
        within the merge radius, or starts a new one. Clusters are found through a grid on their centres; reports or
        clusters with poor GPS (a merge radius wider than a grid cell) are compared with everything, so the result
        is exactly that of comparing every report with every cluster."""
        cfg = self.cfg
        clusters: list[list[Scored]] = []
        # running sums of latitude, longitude and stated accuracy: the same values as summing the cluster each time
        sums: list[list[float]] = []
        lat0 = sum(x.event.lat for x in items) / len(items) if items else 0.0
        grid, cell_of, wide = _Grid(lat0), [], set()       # wide: clusters whose mean accuracy is poor
        half = (GRID_REACH_M - cfg.merge_radius_m) / 2      # each half of the merge radius' accuracy term

        def place(i: int) -> None:
            n = len(clusters[i])
            if 0.5 * sums[i][2] / n > half:
                wide.add(i)
                return
            wide.discard(i)
            key = grid.key(sums[i][0] / n, sums[i][1] / n)
            if cell_of[i] != key:
                if cell_of[i] is not None:
                    grid.cells[cell_of[i]].remove(i)
                grid.cells[key].append(i)
                cell_of[i] = key

        for s in items:
            if 0.5 * s.event.acc_m > half:
                candidates = range(len(clusters))            # poor GPS: compare with every cluster
            else:
                i0, j0 = grid.key(s.event.lat, s.event.lon)
                near = [i for di in (-1, 0, 1) for dj in (-1, 0, 1) for i in grid.cells.get((i0 + di, j0 + dj), ())]
                candidates = sorted(set(near) | wide)
            target = None
            for i in candidates:
                cl = clusters[i]
                n = len(cl)
                lat, lon = sums[i][0] / n, sums[i][1] / n
                head = cl[0].event
                # the merge radius grows with the GPS accuracy the reports themselves state
                radius = cfg.merge_radius_m + 0.5 * (s.event.acc_m + sums[i][2] / n)
                if (s.event.type == head.type
                        and distance_m(lat, lon, s.event.lat, s.event.lon) <= radius
                        and heading_diff(s.event.heading_deg, head.heading_deg) <= cfg.heading_tol_deg):
                    target = i
                    break
            if target is None:
                clusters.append([])
                sums.append([0.0, 0.0, 0.0])
                cell_of.append(None)
                target = len(clusters) - 1
            clusters[target].append(s)
            sums[target][0] += s.event.lat
            sums[target][1] += s.event.lon
            sums[target][2] += s.event.acc_m
            place(target)
        return clusters

    def _decide_cluster(self, hid: str, cluster: list[Scored], now: float) -> Decision:
        cfg = self.cfg
        lat, lon = _centroid(cluster)
        head = cluster[0].event
        heading = head.heading_deg
        first_seen = min(s.event.t for s in cluster)
        reasons: list[str] = []

        # one piece of support per source (a vehicle reporting twice still counts once)
        best: dict[str, Scored] = {}
        for s in cluster:
            if s.event.source not in best or s.trust.weight > best[s.event.source].trust.weight:
                best[s.event.source] = s
        if len(cluster) > 1:
            reasons.append(f"{len(cluster)} reports from {len(best)} source(s) merged into one hazard "
                           f"(within {cfg.merge_radius_m:.0f} m, same direction)")
        for src, s in best.items():
            for r in s.check.reasons:
                reasons.append(f"{src}: {r}")

        support = [Evidence(src, "support", s.trust, s.event.event_id) for src, s in best.items()]

        # negative evidence: drove over the spot, detector on, reported nothing.
        # A vehicle that reported this hazard type nearby (even if its report landed in a
        # neighbouring cluster because of GPS error) is never counted against it.
        reported_nearby = {e.source for e in self._event_grid.near(lat, lon)
                           if e.type == head.type
                           and distance_m(lat, lon, e.lat, e.lon) <= 3 * cfg.merge_radius_m + e.acc_m
                           and heading_diff(e.heading_deg, heading) <= cfg.heading_tol_deg}
        contra: list[Evidence] = []
        slowing: list[Evidence] = []
        # only vehicles near the hazard, in the same order as self.traces (the order the evidence is summed in)
        for vid in sorted(set(self._vehicle_grid.near(lat, lon)), key=self._order.__getitem__):
            trace = self.traces[vid]
            if vid in best or vid in reported_nearby or not could_pass(self._reach.get(vid), lat, lon):
                continue
            t_pass = pass_time(trace, lat, lon, heading, cfg, self._near_steps(vid, lat, lon))
            if t_pass is None or t_pass < first_seen - cfg.pass_window_s:
                continue
            g, _ = trace_consistency(trace, cfg)
            if cfg.fleet_slowing and braked_at(trace, t_pass, cfg):
                slowing.append(Evidence(vid, "slowing", Trust(c=cfg.slow_confidence, g=g, h=self.rep.score(vid),
                                                              f=freshness(now - t_pass, head.type, cfg))))
                continue
            contra.append(Evidence(vid, "contra", Trust(c=cfg.pass_confidence, g=g, h=self.rep.score(vid),
                                                        f=freshness(now - t_pass, head.type, cfg))))

        # independence: identities whose traces move as one are one source
        groups = (self._independence_groups(support) + self._independence_groups(contra)
                  + self._independence_groups(slowing))
        collapsed = [g for g in groups if len(g) > 1]
        sybil = False
        for g in collapsed:
            members = ", ".join(e.source for e in g)
            sep = _mean_separation(self.traces[g[0].source], self.traces[g[1].source])
            sep_txt = f" (mean separation {sep:.1f} m)" if sep is not None else ""
            note = f"{members} move as one{sep_txt}: counted as one source"
            if all(self.rep.is_new(e.source) for e in g):
                note += "; all identities are new"
            reasons.append(note)
            if g[0].kind == "support" and len(g) >= 3:
                sybil = True

        def total(side: list[Evidence]) -> tuple[float, int]:
            by_group: dict[int, float] = {}
            for e in side:
                by_group[e.group] = max(by_group.get(e.group, 0.0), e.weight)
            return sum(by_group.values()), len(by_group)

        sup_w, n_sources = total(support)
        if slowing and n_sources < 2:
            # a lone report gets no help from braking traffic (people brake at crossings too): the vehicles that
            # braked count against it like any other pass-by, exactly as without fleet slowing
            contra += [Evidence(e.source, "contra", Trust(c=cfg.pass_confidence, g=e.trust.g, h=e.trust.h, f=e.trust.f),
                                group=e.group) for e in slowing]
            slowing = []
        con_w, n_contra = total(contra)
        slow_w, n_slow = total(slowing)
        slow_w = min(slow_w, sup_w)          # supporting only: at most doubles what the devices reported
        net = sup_w + slow_w - cfg.lam * con_w
        if slowing:
            reasons.append(f"{n_slow} independent vehicle(s) braked at the spot and sped up again: +{slow_w:.2f} "
                           f"(supporting only, not a source)")
        if contra:
            reasons.append(f"{n_contra} independent vehicle(s) drove over the spot with the detector on and "
                           f"reported nothing: −{cfg.lam * con_w:.2f}")

        best_f = max(s.trust.f for s in best.values())
        if best_f < cfg.expire_freshness:
            state = HazardState.EXPIRED
        elif n_sources >= 2 and net >= cfg.confirm_net:
            state = HazardState.CONFIRMED
        elif n_sources >= 2 and net >= cfg.corroborate_net:
            state = HazardState.CORROBORATED
        elif net >= cfg.unconfirmed_net:
            state = HazardState.UNCONFIRMED
        else:
            state = HazardState.SUPPRESSED
        reasons.append(f"net evidence {net:+.2f} from {n_sources} independent source(s) → {state.value}")

        cold = self._cold_start(state, best, contra, sybil)
        if cold:
            state = HazardState.UNCONFIRMED
            reasons.append(cold)

        severity, state, note = self._severity_scope(state, best)
        if note:
            reasons.append(note)

        return Decision(hid, head.type, lat, lon, heading, head.road_segment, first_seen, state, net,
                        n_sources, support, contra, [[e.source for e in g] for g in collapsed], reasons, sybil,
                        severity, slowing)

    def _severity_scope(self, state: HazardState, best: dict[str, Scored]) -> tuple[str, HazardState, str | None]:
        """Only hazards that make a driver stop, slow down or swerve are warned about (Config.warn_min_severity).
        A report's severity counts as measured when it is above "low" or comes with an IMU jolt; a "low" report
        without a jolt is camera-only, size unknown. Returns (hazard severity, state, reason or None)."""
        rank = {"low": 0, "medium": 1, "high": 2}
        need = rank[self.cfg.warn_min_severity]
        measured = [s.event.severity for s in best.values()
                    if s.event.severity != "low" or s.event.imu_peak_g is not None]
        severity = max(measured, key=rank.__getitem__) if measured else "unknown"
        if state not in (HazardState.UNCONFIRMED, HazardState.CORROBORATED, HazardState.CONFIRMED):
            return severity, state, None
        if measured and rank[severity] >= need:
            return severity, state, None
        if len(measured) == len(best):          # every report measured it, all below the threshold
            return severity, HazardState.SUPPRESSED, (
                f"severity: every report measured it as {severity}, below {self.cfg.warn_min_severity}: "
                "not big enough to make a driver stop, slow down or swerve → suppressed")
        if state != HazardState.UNCONFIRMED:   # camera-only (size unknown) reports can give an advisory at most
            return severity, HazardState.UNCONFIRMED, (
                f"severity: no report measured it at {self.cfg.warn_min_severity} or above (camera only, size "
                "unknown) → advisory until a vehicle's IMU measures it")
        return severity, state, None

    def _cold_start(self, state: HazardState, best: dict[str, Scored], contra: list[Evidence],
                    sybil: bool) -> str | None:
        """SN-09: a lone new source may raise an advisory, and nothing more. Returns the reason, or None."""
        cfg = self.cfg
        if not cfg.cold_start_advisory or state != HazardState.SUPPRESSED or contra or sybil or len(best) != 1:
            return None
        (src, s), = best.items()
        if not self.rep.is_new(src) or s.trust.c < cfg.cold_start_min_c or s.trust.g < cfg.cold_start_min_g:
            return None
        return (f"cold start: {src} is a new source (reliability {s.trust.h:.2f}) and the only report, with "
                f"confidence {s.trust.c:.2f} ≥ {cfg.cold_start_min_c}, consistency {s.trust.g:.2f} ≥ "
                f"{cfg.cold_start_min_g} and no contrary evidence → advisory only (SN-09)")

    def _independence_groups(self, side: list[Evidence]) -> list[list[Evidence]]:
        """Union-find over identities whose position traces are near-identical."""
        parent = list(range(len(side)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, j in itertools.combinations(range(len(side)), 2):
            a, b = self.traces.get(side[i].source, []), self.traces.get(side[j].source, [])
            sep = _mean_separation(a, b, self.cfg.sybil_min_overlap)
            if sep is not None and sep <= self.cfg.sybil_max_sep_m:
                parent[find(i)] = find(j)

        groups: dict[int, list[Evidence]] = defaultdict(list)
        base = id(side)  # unique per side so support and contra groups never merge
        for i, e in enumerate(side):
            e.group = hash((base, find(i)))
            groups[find(i)].append(e)
        return list(groups.values())

    # ---------- learning ----------
    def settle(self, report: Report) -> list[str]:
        """Update source reputation, but only from confident outcomes."""
        lines: list[str] = []

        def log(vid: str, good: bool, amount: float, why: str) -> None:
            before, after = self.rep.record(vid, good, amount)
            lines.append(f"{vid} reliability {before:.3f} → {after:.3f} ({why})")

        for d in report.decisions:
            contra_groups = len({e.group for e in d.contra})
            if d.state == HazardState.CONFIRMED:
                for e in d.support:
                    log(e.source, True, 1.0, f"report on {d.hazard_id} confirmed")
                for e in d.contra:
                    log(e.source, False, 0.5, f"missed confirmed {d.hazard_id}")
            elif d.state == HazardState.SUPPRESSED and (contra_groups >= 2 or d.sybil_suspected):
                for e in d.support:
                    log(e.source, False, 1.0, f"report on {d.hazard_id} contradicted")
        return lines


# ---------- helpers ----------
GRID_CELL_M = 100.0     # index cell size
GRID_REACH_M = 80.0     # items that reach further than this are checked for every hazard (20 % margin for the
                        # small difference between a cell's size and the distance formula across latitudes)


class _Grid:
    """Items in about 100 m cells; near() returns the items in the 3 x 3 cells around a point, plus the items that
    reach further than a cell (`always`)."""

    def __init__(self, lat0: float):
        self.dlat = math.degrees(GRID_CELL_M / EARTH_R)
        self.dlon = math.degrees(GRID_CELL_M / (EARTH_R * max(0.1, math.cos(math.radians(lat0)))))
        self.cells: dict[tuple[int, int], list] = defaultdict(list)
        self.always: list = []

    def key(self, lat: float, lon: float) -> tuple[int, int]:
        return math.floor(lat / self.dlat), math.floor(lon / self.dlon)

    def add(self, lat: float, lon: float, item) -> None:
        self.cells[self.key(lat, lon)].append(item)

    def near(self, lat: float, lon: float):
        i, j = self.key(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                yield from self.cells.get((i + di, j + dj), ())
        yield from self.always

def _centroid(cluster: list[Scored]) -> tuple[float, float]:
    return (sum(s.event.lat for s in cluster) / len(cluster),
            sum(s.event.lon for s in cluster) / len(cluster))


def trace_reach(trace: list[Beacon], cfg: Config) -> tuple[float, float, float, float] | None:
    """Bounding box (lat/lon) of the points where `trace` could have driven over a hazard: the trace's own box,
    widened by its longest step plus the pass radius. pass_time() can only succeed for a point inside it: a
    crossing step has an endpoint within (step length + pass radius) of the point. Lets decide() skip far-away
    vehicles without changing any result."""
    if len(trace) < 2:
        return None
    step = max(distance_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(trace, trace[1:]))
    pad = step + cfg.pass_radius_m + 1.0
    lats = [b.lat for b in trace]
    lons = [b.lon for b in trace]
    dlat = pad / 111320.0
    dlon = pad / (111320.0 * max(0.1, math.cos(math.radians(sum(lats) / len(lats)))))
    return min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon


def could_pass(reach: tuple[float, float, float, float] | None, lat: float, lon: float) -> bool:
    return reach is not None and reach[0] <= lat <= reach[1] and reach[2] <= lon <= reach[3]


def pass_time(trace: list[Beacon], lat: float, lon: float, heading: float, cfg: Config,
              steps: list[int] | None = None) -> float | None:
    """Time a vehicle drove over a point (same direction, detector on), or None. `steps` limits the check to these
    steps (index k = trace[k] -> trace[k + 1], in time order); the first crossing found is the same."""
    pairs = zip(trace, trace[1:]) if steps is None else ((trace[k], trace[k + 1]) for k in steps)
    for a, b in pairs:
        if not (a.detector_on and b.detector_on):
            continue
        if heading_diff(a.heading_deg, heading) > cfg.heading_tol_deg:
            continue
        a_along, a_across = along_and_across(a.lat, a.lon, lat, lon, heading)
        b_along, b_across = along_and_across(b.lat, b.lon, lat, lon, heading)
        if a_along <= 0 <= b_along and min(abs(a_across), abs(b_across)) <= cfg.pass_radius_m:
            frac = -a_along / (b_along - a_along) if b_along != a_along else 0.0
            return a.t + frac * (b.t - a.t)
    return None


def braked_at(trace: list[Beacon], t_pass: float, cfg: Config) -> bool:
    """True if the vehicle braked sharply around t_pass and sped up again (Config.fleet_slowing): its highest speed
    before the spot, its lowest speed near it and its highest speed after it, from the 1 Hz position messages. A
    full stop or a slow stretch without recovery (a jam) is not braking for a hazard."""
    def speeds(lo: float, hi: float) -> list[float]:
        return [b.speed_mps for b in trace if lo <= b.t - t_pass <= hi]
    before = speeds(-cfg.slow_before_s[1], -cfg.slow_before_s[0])
    near = speeds(-cfg.slow_near_s, cfg.slow_near_s)
    after = speeds(cfg.slow_after_s[0], cfg.slow_after_s[1])
    if not (before and near and after):
        return False
    v0, vmin, v1 = max(before), min(near), max(after)
    return (v0 * 3.6 >= cfg.slow_from_kmh and vmin <= (1 - cfg.slow_drop) * v0
            and vmin * 3.6 >= cfg.slow_stop_kmh and v1 - vmin >= cfg.slow_recover * (v0 - vmin))


def _mean_separation(a: list[Beacon], b: list[Beacon], min_overlap: int = 1) -> float | None:
    """Mean distance between two traces at the seconds they both report."""
    pa = {round(x.t): x for x in a}
    common = [(pa[round(y.t)], y) for y in b if round(y.t) in pa]
    if len(common) < min_overlap:
        return None
    return sum(distance_m(p.lat, p.lon, q.lat, q.lon) for p, q in common) / len(common)
