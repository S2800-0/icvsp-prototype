"""The decision engine: Safety Events + position beacons in, hazard decisions out.

Steps inside Engine.decide():
  1. replay protection and plausibility checks on every event
  2. trust weight for every surviving event
  3. merge reports of the same hazard (type, place, direction)
  4. find negative evidence: vehicles that drove over the spot, detector on, and said nothing
  5. independence check: identities that move as one are counted once (Sybil defence)
  6. net evidence → hazard state, with a written reason for every step
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config
from .geo import along_and_across, distance_m, heading_diff
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
            self.malformed.append(Rejected(str(d.get("event_id", "?")), str(d.get("source", "?")),
                                           ["schema: " + e for e in errs]))
            return False
        self.inbox.append(SafetyEvent.from_dict(d))
        return True

    # ---------- decision ----------
    def decide(self, now: float) -> Report:
        cfg = self.cfg
        for tr in self.traces.values():
            tr.sort(key=lambda b: b.t)
        report = Report(now=now, rejected=list(self.malformed))

        accepted: list[Scored] = []
        seen_ids: set[str] = set()
        for ev in sorted(self.inbox, key=lambda e: e.t):
            if ev.event_id in seen_ids:
                report.rejected.append(Rejected(ev.event_id, ev.source, ["duplicate event_id (replay protection)"]))
                continue
            seen_ids.add(ev.event_id)
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

    def _cluster(self, items: list[Scored]) -> list[list[Scored]]:
        clusters: list[list[Scored]] = []
        for s in items:
            for cl in clusters:
                lat, lon = _centroid(cl)
                head = cl[0].event
                # the merge radius grows with the GPS accuracy the reports themselves state
                radius = self.cfg.merge_radius_m + 0.5 * (s.event.acc_m + sum(x.event.acc_m for x in cl) / len(cl))
                if (s.event.type == head.type
                        and distance_m(lat, lon, s.event.lat, s.event.lon) <= radius
                        and heading_diff(s.event.heading_deg, head.heading_deg) <= self.cfg.heading_tol_deg):
                    cl.append(s)
                    break
            else:
                clusters.append([s])
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
        reported_nearby = {e.source for e in self.inbox
                           if e.type == head.type
                           and distance_m(lat, lon, e.lat, e.lon) <= 3 * cfg.merge_radius_m + e.acc_m
                           and heading_diff(e.heading_deg, heading) <= cfg.heading_tol_deg}
        contra: list[Evidence] = []
        for vid, trace in self.traces.items():
            if vid in best or vid in reported_nearby:
                continue
            t_pass = pass_time(trace, lat, lon, heading, cfg)
            if t_pass is None or t_pass < first_seen - cfg.pass_window_s:
                continue
            g, _ = trace_consistency(trace, cfg)
            contra.append(Evidence(vid, "contra", Trust(c=cfg.pass_confidence, g=g, h=self.rep.score(vid),
                                                        f=freshness(now - t_pass, head.type, cfg))))

        # independence: identities whose traces move as one are one source
        groups = self._independence_groups(support) + self._independence_groups(contra)
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
        con_w, n_contra = total(contra)
        net = sup_w - cfg.lam * con_w
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

        return Decision(hid, head.type, lat, lon, heading, head.road_segment, first_seen, state, net,
                        n_sources, support, contra, [[e.source for e in g] for g in collapsed], reasons, sybil)

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
def _centroid(cluster: list[Scored]) -> tuple[float, float]:
    return (sum(s.event.lat for s in cluster) / len(cluster),
            sum(s.event.lon for s in cluster) / len(cluster))


def pass_time(trace: list[Beacon], lat: float, lon: float, heading: float, cfg: Config) -> float | None:
    """Time a vehicle drove over a point (same direction, detector on), or None."""
    for a, b in zip(trace, trace[1:]):
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


def _mean_separation(a: list[Beacon], b: list[Beacon], min_overlap: int = 1) -> float | None:
    """Mean distance between two traces at the seconds they both report."""
    pa = {round(x.t): x for x in a}
    common = [(pa[round(y.t)], y) for y in b if round(y.t) in pa]
    if len(common) < min_overlap:
        return None
    return sum(distance_m(p.lat, p.lon, q.lat, q.lon) for p, q in common) / len(common)
