"""The spatial shortcut in decide() (trace_reach / could_pass) never changes a result (NFR-02 support)."""
import math
import random
import unittest

from unittest import mock

import icvsp.consensus as consensus
from icvsp.config import Config
from icvsp.consensus import Engine, could_pass, pass_time, trace_reach
from icvsp.geo import offset
from icvsp.model import Beacon, SafetyEvent
from icvsp.trust import Reputation


class SpatialShortcut(unittest.TestCase):
    def test_skipped_vehicles_could_never_have_passed(self):
        rng, cfg = random.Random(3), Config()
        checked = passed = 0
        for _ in range(400):
            lat, lon = offset(30.0444, 31.2357, rng.uniform(-300, 300), rng.uniform(-300, 300))
            heading, t, trace = rng.uniform(0, 360), 0.0, []
            for _ in range(rng.randint(2, 40)):
                trace.append(Beacon("v", t, lat, lon, 10.0, heading, True))
                step = rng.choice([5.0, 12.0, 40.0, 150.0])      # includes gaps after lost positions
                heading = (heading + rng.uniform(-30, 30)) % 360
                lat, lon = offset(lat, lon, step * math.cos(math.radians(heading)), step * math.sin(math.radians(heading)))
                t += 1.0
            reach = trace_reach(trace, cfg)
            for _ in range(25):
                plat, plon = offset(30.0444, 31.2357, rng.uniform(-400, 400), rng.uniform(-400, 400))
                hd = rng.choice([b.heading_deg for b in trace])
                if not could_pass(reach, plat, plon):
                    checked += 1
                    self.assertIsNone(pass_time(trace, plat, plon, hd, cfg))
                elif pass_time(trace, plat, plon, hd, cfg) is not None:
                    passed += 1
        self.assertGreater(checked, 1000)
        self.assertGreater(passed, 10)


def random_city(seed: int, cars: int = 150) -> Engine:
    """Cars on 8 shared east-west roads (1 Hz positions, some lost), each reporting some of the hazards it drives
    over, so other cars also drive past hazards without reporting them."""
    rng, cfg = random.Random(seed), Config()
    rep = Reputation(cfg.prior_good, cfg.prior_bad)
    eng = Engine(cfg, rep)
    now = 1790000000.0
    roads = [offset(30.0444, 31.2357, 400.0 * r, 0.0)[0] for r in range(8)]
    hazards = [(rng.randrange(8), rng.uniform(-1200, 1200), rng.choice(["pothole", "speed_bump"])) for _ in range(60)]
    for k in range(cars):
        vid = f"car-{k}"
        rep.seed(vid, rng.choice([0.0, 3.0]), 0.0)
        road, east0, direction = rng.randrange(8), rng.uniform(-1500, -900), rng.choice([1, -1])
        east0 = east0 if direction == 1 else -east0
        heading, t0, seq = (90.0 if direction == 1 else 270.0), now - 300 + rng.uniform(0, 100), 0
        for t in range(220):
            if rng.random() < 0.05:
                continue                                  # a lost position: a longer step
            lat, lon = offset(roads[road], 31.2357, 0.0, east0 + direction * 11.0 * t)
            eng.ingest_beacon(Beacon(vid, t0 + t, lat, lon, 11.0, heading, True))
        for hroad, east, typ in hazards:
            if hroad != road or rng.random() < 0.6:
                continue
            t = (east - east0) / (direction * 11.0)
            if not 0 < t < 220:
                continue
            seq += 1
            lat, lon = offset(roads[road], 31.2357, rng.gauss(0, 2), east + rng.gauss(0, 2))
            acc = rng.choice([3.0, 5.0, 25.0])            # some poor GPS reaches beyond a grid cell
            eng.ingest_dict(SafetyEvent(f"{vid}-{seq}", typ, t0 + t, lat, lon, acc, f"road-{road}", 40.0, heading, "high",
                                        0.9, vid, imu_peak_g=0.8, boot_id=1, sequence=seq).to_dict())
    return eng


class GridIndex(unittest.TestCase):
    def test_grid_index_gives_exactly_the_same_decisions(self):
        for seed in (1, 2):
            fast = random_city(seed).decide(1790000000.0)
            with mock.patch.object(consensus, "GRID_REACH_M", -1.0):      # everything checked everywhere
                slow = random_city(seed).decide(1790000000.0)
            key = lambda d: (d.hazard_id, d.state, round(d.net, 9), d.independent_sources,  # noqa: E731
                             sorted(e.source for e in d.support), sorted(e.source for e in d.contra), d.reasons)
            self.assertEqual([key(d) for d in fast.decisions], [key(d) for d in slow.decisions])
            self.assertGreater(sum(len(d.contra) for d in fast.decisions), 5)   # pass-bys are exercised


if __name__ == "__main__":
    unittest.main()
