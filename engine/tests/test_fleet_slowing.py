"""Fleet slowing: vehicles that brake at a reported spot and speed up again support the hazard (supporting only)."""
import dataclasses
import unittest

from icvsp.config import Config
from icvsp.consensus import braked_at
from icvsp.geo import distance_m
from icvsp.model import Beacon, HazardState
from icvsp.pipeline import evaluate_traces
from icvsp.sim import SCENARIOS, run

WARNING = (HazardState.CORROBORATED, HazardState.CONFIRMED)
CRUISE = 11.0   # m/s, about 40 km/h


def trace(profile, t_pass=100.0):
    """1 Hz beacons with speed = profile(seconds from the pass)."""
    return [Beacon("v", t_pass + k, 30.0, 31.0, profile(k), 90.0) for k in range(-15, 16)]


def bump(k):     # brakes to 15 km/h at the spot, back to cruise after
    return CRUISE if abs(k) > 4 else max(4.2, CRUISE * abs(k) / 4)


def stop(k):     # stops at a crossing
    return CRUISE if abs(k) > 4 else 0.0 if abs(k) <= 1 else CRUISE * abs(k) / 4


def jam(k):      # slow from the spot on, no recovery
    return CRUISE if k < -3 else 3.0


def with_profile(key, profile, vehicles=None, second_reporter=False):
    """Scenario `key`, with the passing vehicles (or `vehicles`) driving `profile` around the reported spot.
    second_reporter: the first passing vehicle also reports the hazard (two independent sources)."""
    scn = SCENARIOS[key]
    out = run(scn, 7)
    ev = out.events[0]
    if second_reporter:
        passer = sorted({b.vehicle_id for b in out.beacons} - {e.source for e in out.events})[0]
        near = min((b for b in out.beacons if b.vehicle_id == passer),
                   key=lambda b: distance_m(b.lat, b.lon, ev.lat, ev.lon))
        out.events.append(dataclasses.replace(ev, event_id=f"{passer}-1-1", source=passer, t=near.t,
                                              lat=near.lat, lon=near.lon, sequence=1))
    reporters = {e.source for e in out.events}
    by_vid = {}
    for b in out.beacons:
        by_vid.setdefault(b.vehicle_id, []).append(b)
    new = []
    for vid, bs in by_vid.items():
        if vid in reporters or (vehicles is not None and vid not in vehicles):
            new += bs
            continue
        t0 = min(bs, key=lambda b: distance_m(b.lat, b.lon, ev.lat, ev.lon)).t
        new += [dataclasses.replace(b, speed_mps=profile(round(b.t - t0))) for b in bs]
    out.beacons = new
    return scn, out


class BrakedAt(unittest.TestCase):
    def test_braking_for_a_bump_and_speeding_up_again(self):
        self.assertTrue(braked_at(trace(bump), 100.0, Config()))

    def test_steady_speed_is_not_braking(self):
        self.assertFalse(braked_at(trace(lambda k: CRUISE), 100.0, Config()))

    def test_a_full_stop_is_a_crossing_not_a_hazard(self):
        self.assertFalse(braked_at(trace(stop), 100.0, Config()))

    def test_a_jam_without_recovery_is_not_braking_for_a_hazard(self):
        self.assertFalse(braked_at(trace(jam), 100.0, Config()))

    def test_already_slow_traffic_is_not_counted(self):
        self.assertFalse(braked_at(trace(lambda k: bump(k) * 0.4), 100.0, Config()))


class Decisions(unittest.TestCase):
    def test_passing_vehicles_that_braked_support_a_hazard_two_devices_reported(self):
        base = evaluate_traces(*with_profile("false_report", lambda k: CRUISE, second_reporter=True),
                               Config()).report.decisions[0]
        braked = evaluate_traces(*with_profile("false_report", bump, second_reporter=True), Config()).report.decisions[0]
        self.assertEqual(base.independent_sources, 2)
        self.assertEqual(len(base.slowing), 0)
        self.assertGreater(len(base.contra), 0)
        self.assertEqual(len(braked.contra), 0)
        self.assertEqual(len(braked.slowing), len(base.contra))
        self.assertGreater(braked.net, base.net)
        self.assertTrue(any("braked at the spot" in r for r in braked.reasons))

    def test_a_lone_report_gets_no_help_from_braking_traffic(self):
        # people brake at crossings too: a single (possibly wrong) report there must not be propped up
        base = evaluate_traces(*with_profile("false_report", lambda k: CRUISE), Config()).report.decisions[0]
        d = evaluate_traces(*with_profile("false_report", bump), Config()).report.decisions[0]
        self.assertEqual(d.independent_sources, 1)
        self.assertEqual(len(d.slowing), 0)
        self.assertEqual(len(d.contra), len(base.contra))
        self.assertEqual(d.state, base.state)
        self.assertNotIn(d.state, WARNING)

    def test_slowing_weight_is_capped_at_the_reported_support(self):
        d = evaluate_traces(*with_profile("false_report", bump, second_reporter=True), Config()).report.decisions[0]
        sup = sum(e.weight for e in d.support)
        self.assertLessEqual(d.net, 2 * sup + 1e-9)

    def test_stopping_traffic_still_counts_against_the_report(self):
        d = evaluate_traces(*with_profile("false_report", stop, second_reporter=True), Config()).report.decisions[0]
        self.assertEqual(len(d.slowing), 0)
        self.assertGreater(len(d.contra), 0)

    def test_fleet_slowing_can_be_switched_off(self):
        d = evaluate_traces(*with_profile("false_report", bump, second_reporter=True),
                            Config(fleet_slowing=False)).report.decisions[0]
        self.assertEqual(len(d.slowing), 0)
        self.assertGreater(len(d.contra), 0)


if __name__ == "__main__":
    unittest.main()
