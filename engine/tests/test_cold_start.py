"""Cold start (SN-09, mentor review of FR-07/FR-08/FR-12): a single new vehicle must get value.

A brand-new identity starts at reliability 1/(1+3) = 0.25, so its trust weight c·g·h·f can never
exceed 0.25, below the 0.3 needed for UNCONFIRMED. Without an explicit rule a lone early user
could never produce even an advisory. The cold-start rule allows exactly that, and nothing more.
"""
import unittest
from dataclasses import replace

from icvsp.config import Config
from icvsp.model import HazardState
from icvsp.pipeline import evaluate
from icvsp.sim import Scenario, VehicleSpec


def lone_new_vehicle(confidence=0.9, acc_m=4.0, others=(), hazard=True, role="honest"):
    return Scenario(
        "cold_start_test", "Cold start", "A brand-new vehicle is the only source.",
        [VehicleSpec("N", 0, 400, confidence=confidence, acc_m=acc_m, history=None, role=role),
         VehicleSpec("D", t_start=25, s0=0), *others],
        hazard_s=600.0 if hazard else None, claimed_s=600.0)


def state(scn, cfg=None):
    res = evaluate(scn, cfg)
    return res, res.report.decisions[0]


class ColdStart(unittest.TestCase):
    def test_new_weight_alone_cannot_reach_the_advisory_threshold(self):
        """Documents the gap the rule closes: the maximum weight of a new source."""
        cfg = Config()
        self.assertLess(cfg.prior_good / (cfg.prior_good + cfg.prior_bad), cfg.unconfirmed_net)

    def test_lone_new_vehicle_gets_an_advisory_to_d(self):
        res, d = state(lone_new_vehicle())
        self.assertEqual(d.state, HazardState.UNCONFIRMED)
        self.assertTrue(any("cold start" in r for r in d.reasons))
        warn, _ = res.targets[d.hazard_id]
        self.assertEqual([(t.vehicle_id, t.level) for t in warn], [("D", "advisory")])

    def test_cold_start_never_goes_beyond_an_advisory(self):
        _, d = state(lone_new_vehicle(confidence=1.0))
        self.assertEqual(d.state, HazardState.UNCONFIRMED)

    def test_confidence_boundary(self):
        cfg = Config()
        _, at = state(lone_new_vehicle(confidence=cfg.cold_start_min_c))
        _, below = state(lone_new_vehicle(confidence=cfg.cold_start_min_c - 0.01))
        self.assertEqual(at.state, HazardState.UNCONFIRMED)
        self.assertEqual(below.state, HazardState.SUPPRESSED)

    def test_poor_gps_gets_no_cold_start_advisory(self):
        _, d = state(lone_new_vehicle(acc_m=25.0))
        self.assertEqual(d.state, HazardState.SUPPRESSED)

    def test_any_negative_evidence_blocks_the_cold_start_advisory(self):
        honest_passer = VehicleSpec("B", 0, 300)
        _, d = state(lone_new_vehicle(others=[honest_passer], hazard=False, role="false_report"))
        self.assertTrue(d.contra)
        self.assertEqual(d.state, HazardState.SUPPRESSED)

    def test_sybil_identities_get_no_cold_start_advisory_even_with_no_witness(self):
        scn = Scenario("sybil_alone", "Sybil, nobody passes", "Five new identities on one trajectory.",
                       [VehicleSpec("S", 0, 400, role="sybil", clones=4, confidence=0.95, history=None),
                        VehicleSpec("D", t_start=25, s0=0)], hazard_s=None)
        _, d = state(scn)
        self.assertTrue(d.sybil_suspected)
        self.assertEqual(d.state, HazardState.SUPPRESSED)

    def test_rule_can_be_switched_off(self):
        _, d = state(lone_new_vehicle(), replace(Config(), cold_start_advisory=False))
        self.assertEqual(d.state, HazardState.SUPPRESSED)


if __name__ == "__main__":
    unittest.main()
