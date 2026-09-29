"""Per-report trust weight and per-source reputation.

weight = c · g · h · f
  c  detection confidence (from the vehicle)
  g  consistency (from the plausibility checks)
  h  source reliability (Beta reputation, node-centric)
  f  freshness (exponential decay)

A product means one weak factor is enough to pull the weight down. Whether a
product, a Bayesian update or Dempster–Shafer combination works best is an open
research question (see Raya et al., 2008), which is why the weight lives in one
small function that can be swapped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class Trust:
    c: float
    g: float
    h: float
    f: float

    @property
    def weight(self) -> float:
        return self.c * self.g * self.h * self.f


def freshness(age_s: float, hazard_type: str, cfg: Config) -> float:
    tau = cfg.freshness_tau_s.get(hazard_type, 3600.0)
    return math.exp(-max(0.0, age_s) / tau)


class Reputation:
    """Beta reputation (Jøsang & Ismail, 2002).

    score = (good + a) / (good + bad + a + b), where (a, b) is the prior.
    With the default prior (1, 3) a brand-new identity scores 0.25, so creating
    many fresh identities (a Sybil attack) buys very little weight.
    """

    def __init__(self, prior_good: float = 1.0, prior_bad: float = 3.0):
        self.a, self.b = prior_good, prior_bad
        self.good: dict[str, float] = {}
        self.bad: dict[str, float] = {}

    def seed(self, vid: str, good: float, bad: float) -> None:
        self.good[vid], self.bad[vid] = good, bad

    def score(self, vid: str) -> float:
        g, b = self.good.get(vid, 0.0), self.bad.get(vid, 0.0)
        return (g + self.a) / (g + b + self.a + self.b)

    def is_new(self, vid: str) -> bool:
        return self.good.get(vid, 0.0) + self.bad.get(vid, 0.0) < 3

    def record(self, vid: str, good: bool, amount: float = 1.0) -> tuple[float, float]:
        before = self.score(vid)
        table = self.good if good else self.bad
        table[vid] = table.get(vid, 0.0) + amount
        return before, self.score(vid)
