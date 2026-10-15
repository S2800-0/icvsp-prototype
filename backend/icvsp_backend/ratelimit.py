"""Rate limits (FR-SEC-20): a token bucket per (kind, key).

Each kind allows `per_min` requests per minute on average and short bursts of up to `burst`. The keys are chosen
so an attacker cannot use up someone else's allowance:

    reports_ip      POST /reports per caller address, before any check (junk floods cost no CPU)
    reports_device  POST /reports per device, counted only AFTER the signature is verified, so a forged
                    sender cannot block a real device
    beacons_user    POST /beacons per signed-in user
    hazards_user    GET /hazards per signed-in user
    auth_fail_ip    failed sign-ins per caller address (slows token guessing)
    admin           administrator endpoints per administrator

Every limit is configurable without code changes:  ICVSP_RATE_REPORTS_DEVICE=30/10  (per minute / burst), or
ICVSP_RATE_REPORTS_IP=off. Over the limit the API answers 429 with Retry-After, and the event is logged.

The buckets live in memory, which is right for one server; several servers behind a load balancer need a
shared store (e.g. Redis) or limits at the gateway (API Gateway / WAF).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("icvsp.security")


@dataclass(frozen=True)
class Limit:
    per_min: float
    burst: int


# Starting values: a phone uploads positions in 10 s batches (6 per minute) and reports a few hazards per trip;
# the limits leave room for retries after an outage and for queue flushes.
DEFAULTS: dict[str, Limit | None] = {
    "reports_ip": Limit(120, 60),
    "reports_device": Limit(30, 20),
    "beacons_user": Limit(30, 20),
    "hazards_user": Limit(60, 20),
    "auth_fail_ip": Limit(10, 10),
    "admin": Limit(120, 60),
}
MAX_KEYS = 50_000            # memory bound: the least recently used buckets are dropped beyond this


def limits_from_env(overrides: dict[str, Limit | None] | None = None) -> dict[str, Limit | None]:
    out = dict(DEFAULTS)
    for kind in DEFAULTS:
        raw = os.environ.get(f"ICVSP_RATE_{kind.upper()}")
        if raw is None:
            continue
        if raw.strip().lower() == "off":
            out[kind] = None
        else:
            per_min, burst = raw.split("/")
            out[kind] = Limit(float(per_min), int(burst))
    out.update(overrides or {})
    return out


class RateLimiter:
    def __init__(self, limits: dict[str, Limit | None] | None = None, clock: Callable[[], float] = time.time):
        self.limits = limits if limits is not None else limits_from_env()
        self.clock = clock
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}   # key -> (tokens, last time)
        self._lock = threading.Lock()

    def _state(self, kind: str, key: str, now: float) -> tuple[float, Limit] | None:
        lim = self.limits.get(kind)
        if lim is None:
            return None
        tokens, last = self._buckets.get((kind, key), (float(lim.burst), now))
        tokens = min(float(lim.burst), tokens + max(0.0, now - last) * lim.per_min / 60.0)
        return tokens, lim

    def hit(self, kind: str, key: str) -> float | None:
        """Counts one request. Returns None if allowed, or the seconds to wait before retrying."""
        now = self.clock()
        with self._lock:
            st = self._state(kind, key, now)
            if st is None:
                return None
            tokens, lim = st
            if tokens >= 1.0:
                self._buckets.pop((kind, key), None)
                self._buckets[(kind, key)] = (tokens - 1.0, now)   # re-insert: most recently used last
                if len(self._buckets) > MAX_KEYS:
                    self._buckets.pop(next(iter(self._buckets)))
                return None
            self._buckets[(kind, key)] = (tokens, now)
            wait = (1.0 - tokens) * 60.0 / lim.per_min
        log.warning("rate_limited ts=%.0f kind=%s key=%s retry_after=%.1f", now, kind, key, wait)
        return wait

    def blocked(self, kind: str, key: str) -> float | None:
        """Like hit() but does not count a request: the wait if the bucket is empty, else None."""
        now = self.clock()
        with self._lock:
            st = self._state(kind, key, now)
        if st is None or st[0] >= 1.0:
            return None
        tokens, lim = st
        return (1.0 - tokens) * 60.0 / lim.per_min
