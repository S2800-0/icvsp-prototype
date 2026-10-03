"""Every tunable number in the engine lives here.

These values are starting guesses, not tuned results. Changing one should be a
deliberate, recorded decision (put the Config you used next to any results).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    # --- plausibility (data-centric checks on each Safety Event) ---
    max_future_s: float = 2.0            # reject events stamped this far in the future
    # older = stale / possible replay. Speed bumps are built and rarely removed, so reports stay valid longer.
    max_age_s: dict = field(default_factory=lambda: {"pothole": 48 * 3600, "speed_bump": 14 * 24 * 3600})
    max_acc_m: float = 30.0              # GPS accuracy worse than this can't be placed on a segment
    good_acc_m: float = 5.0              # accuracy at or better than this gets full consistency
    max_speed_kmh: float = 250.0
    max_trace_speed_mps: float = 70.0    # implied speed between two beacons; above = position jump
    sender_match_dt_s: float = 5.0       # how close in time a sender beacon must be to the event
    sender_max_offset_m: float = 40.0    # event location vs sender's own position at that time

    # --- trust ---
    freshness_tau_s: dict = field(default_factory=lambda: {"pothole": 6 * 3600, "speed_bump": 7 * 24 * 3600})
    prior_good: float = 1.0              # Beta reputation prior: new identities start at
    prior_bad: float = 3.0               # 1 / (1 + 3) = 0.25, i.e. sceptical

    # --- consensus ---
    merge_radius_m: float = 10.0         # reports this close (same type, same direction) = one hazard
    heading_tol_deg: float = 45.0
    pass_radius_m: float = 12.0          # lateral distance for "this vehicle drove over the spot"
    pass_window_s: float = 60.0          # pass-bys this long before the first report still count
    pass_confidence: float = 0.9         # how much a "saw nothing" pass is worth vs a detection
    lam: float = 0.8                     # weight of negative evidence (λ)
    sybil_max_sep_m: float = 2.0         # identities closer than this on average move "as one"
    sybil_min_overlap: int = 3           # beacons in common needed to compare two traces

    confirm_net: float = 1.2             # ≥2 independent sources and net ≥ this → CONFIRMED
    corroborate_net: float = 0.6         # ≥2 independent sources and net ≥ this → CORROBORATED
    unconfirmed_net: float = 0.3         # net ≥ this → UNCONFIRMED (advisory only)

    # --- cold start (SN-09) ---
    # A new identity's reliability is prior_good / (prior_good + prior_bad) = 0.25, so its weight
    # can never reach unconfirmed_net. Without this rule a lone early user could never warn anyone.
    # The rule allows ONE thing: a single, new, high-quality source with no contrary evidence may
    # raise an UNCONFIRMED advisory. It never raises a warning, and Sybil groups never qualify.
    cold_start_advisory: bool = True
    cold_start_min_c: float = 0.8        # detector confidence needed (starting value, to be tuned)
    cold_start_min_g: float = 0.8        # consistency needed, i.e. good GPS and plausible report
    expire_freshness: float = 0.1        # best supporting freshness below this → EXPIRED

    # --- targeting ---
    warn_horizon_m: float = 500.0
    active_window_s: float = 5.0         # a vehicle is "on the road now" if seen this recently
