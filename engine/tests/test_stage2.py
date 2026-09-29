import random
import unittest

from icvsp.baselines import b1_naive, b2_majority
from icvsp.config import Config
from icvsp.experiment import Counts, SWEEPS, run_sweep, verdicts, wilson
from icvsp.pipeline import evaluate
from icvsp.sim import LANE_M, SCENARIOS, random_scenario, run

CFG = Config()


def baseline_verdicts(key):
    scn = SCENARIOS[key]
    res = evaluate(scn)
    lat, lon = scn.segment.point(scn.claimed_s, LANE_M)
    h = scn.segment.heading_deg
    return (b1_naive(res.sim.events, lat, lon, h, CFG),
            b2_majority(res.sim.events, res.engine.traces, lat, lon, h, CFG))


class Baselines(unittest.TestCase):
    def test_b1_is_fooled_by_a_single_false_report(self):
        b1, _ = baseline_verdicts("false_report")
        self.assertTrue(b1)

    def test_b2_outvotes_one_false_report(self):
        _, b2 = baseline_verdicts("false_report")
        self.assertFalse(b2)

    def test_b2_is_fooled_by_sybil_identities_but_engine_is_not(self):
        _, b2 = baseline_verdicts("sybil")
        self.assertTrue(b2)  # five fake votes beat one honest "saw nothing"
        scn = SCENARIOS["sybil"]
        v = verdicts(scn, run(scn), CFG)
        self.assertFalse(v["Engine"])

    def test_all_deciders_warn_on_a_well_reported_real_hazard(self):
        scn = SCENARIOS["duplicates"]
        v = verdicts(scn, run(scn), CFG)
        self.assertTrue(v["B1 naive"] and v["B2 majority"] and v["Engine"])


class NoisyGps(unittest.TestCase):
    def test_a_vehicle_that_reported_is_never_counted_as_saw_nothing(self):
        """Regression: with noisy GPS, reports of one pothole could split into two clusters and
        each reporter was then counted as negative evidence against the other cluster."""
        import copy
        for seed in range(20):
            scn = copy.deepcopy(SCENARIOS["duplicates"])
            scn.gps_sigma_m = 5.0
            for v in scn.vehicles:
                v.acc_m = 10.0
            res = evaluate(scn, seed=seed)
            reporters = {e.source for e in res.sim.events}
            for d in res.report.decisions:
                self.assertFalse({e.source for e in d.contra} & reporters, f"seed {seed}")
            self.assertTrue(any(d.state.value == "confirmed" for d in res.report.decisions), f"seed {seed}")


class Metrics(unittest.TestCase):
    def test_counts_and_rates(self):
        c = Counts(tp=8, fp=2, tn=9, fn=1)
        m = c.metrics()
        self.assertAlmostEqual(m["false_alert_rate"], 0.2)
        self.assertAlmostEqual(m["missed_rate"], 1 / 9)
        self.assertAlmostEqual(m["accuracy"], 17 / 20)

    def test_wilson_interval_contains_the_estimate_and_stays_in_range(self):
        lo, hi = wilson(0, 50)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0)
        lo, hi = wilson(30, 100)
        self.assertLess(lo, 0.3)
        self.assertGreater(hi, 0.3)

    def test_experiment_is_reproducible(self):
        sw = SWEEPS[0]
        a = run_sweep(sw, runs=15, seed=11, cfg=CFG)
        b = run_sweep(sw, runs=15, seed=11, cfg=CFG)
        self.assertEqual(str(a), str(b))  # str() so NaN rates compare equal

    def test_random_scenarios_have_ground_truth_both_ways(self):
        rng = random.Random(3)
        real = {random_scenario(rng).hazard_s is not None for _ in range(40)}
        self.assertEqual(real, {True, False})


if __name__ == "__main__":
    unittest.main()
