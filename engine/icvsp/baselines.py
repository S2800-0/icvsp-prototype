"""The two baselines every result is compared against (proposal §12).

Both see exactly the same messages as the engine and use the same geometry for
"did this vehicle drive over the spot". What they lack is the engine's
plausibility checks, trust weights, reputation and independence check.

  B1  naive forwarding: warn if anyone reported the hazard
  B2  unweighted majority vote: among vehicles that drove over the spot, warn if
      more reported it than didn't (every identity is one vote)
"""
from __future__ import annotations

from .config import Config
from .consensus import pass_time
from .geo import distance_m, heading_diff
from .model import Beacon, SafetyEvent


def reporters_near(events: list[SafetyEvent], lat: float, lon: float, heading: float, cfg: Config) -> set[str]:
    return {e.source for e in events
            if distance_m(e.lat, e.lon, lat, lon) <= cfg.merge_radius_m * 1.5
            and heading_diff(e.heading_deg, heading) <= cfg.heading_tol_deg}


def b1_naive(events: list[SafetyEvent], lat: float, lon: float, heading: float, cfg: Config) -> bool:
    return bool(reporters_near(events, lat, lon, heading, cfg))


def b2_majority(events: list[SafetyEvent], traces: dict[str, list[Beacon]], lat: float, lon: float,
                heading: float, cfg: Config) -> bool:
    yes = reporters_near(events, lat, lon, heading, cfg)
    if not yes:
        return False
    no = {vid for vid, tr in traces.items()
          if vid not in yes and pass_time(sorted(tr, key=lambda b: b.t), lat, lon, heading, cfg) is not None}
    return len(yes) > len(no)
