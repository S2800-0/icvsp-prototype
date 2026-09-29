"""Risk engine: which vehicles are actually exposed to a hazard right now?"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .consensus import Decision
from .geo import along_and_across, heading_diff
from .model import Beacon, HazardState


@dataclass
class Target:
    vehicle_id: str
    level: str          # "warning" or "advisory"
    distance_m: float
    eta_s: float | None


@dataclass
class Skipped:
    vehicle_id: str
    reason: str


def targets(d: Decision, traces: dict[str, list[Beacon]], now: float, cfg: Config) -> tuple[list[Target], list[Skipped]]:
    if d.state in (HazardState.SUPPRESSED, HazardState.EXPIRED):
        return [], []
    level = "advisory" if d.state == HazardState.UNCONFIRMED else "warning"
    reporters = {e.source for e in d.support}
    warn: list[Target] = []
    skip: list[Skipped] = []
    for vid, trace in traces.items():
        if vid in reporters or not trace:
            continue
        b = trace[-1]
        if now - b.t > cfg.active_window_s:
            continue  # not on the road right now
        along, across = along_and_across(b.lat, b.lon, d.lat, d.lon, d.heading_deg)
        if heading_diff(b.heading_deg, d.heading_deg) > cfg.heading_tol_deg:
            skip.append(Skipped(vid, "opposite direction"))
        elif abs(across) > cfg.pass_radius_m:
            skip.append(Skipped(vid, "different road"))
        elif along > 0:
            skip.append(Skipped(vid, "already past the hazard"))
        elif -along > cfg.warn_horizon_m:
            skip.append(Skipped(vid, f"beyond the {cfg.warn_horizon_m:.0f} m warning horizon"))
        else:
            eta = -along / b.speed_mps if b.speed_mps > 0.5 else None
            warn.append(Target(vid, level, -along, eta))
    return warn, skip
