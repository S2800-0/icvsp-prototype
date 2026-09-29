"""The scripted scenarios are the specification: each one must reach its expected outcome."""
import unittest

from icvsp.model import HazardState
from icvsp.pipeline import evaluate
from icvsp.sim import EXPECTED, SCENARIOS


def run(key):
    return evaluate(SCENARIOS[key])


def main_decision(res):
    """The hazard in the observed lane (the one vehicle D could be warned about)."""
    assert res.report.decisions, "expected at least one hazard"
    return res.report.decisions[0]


class ExpectedStates(unittest.TestCase):
    def test_every_scenario_reaches_its_expected_state(self):
        for key, expected in EXPECTED.items():
            with self.subTest(scenario=key):
                res = run(key)
                if expected is None:
                    self.assertEqual(res.report.decisions, [])
                else:
                    self.assertEqual(main_decision(res).state, expected)


class Scenarios(unittest.TestCase):
    def test_single_report_gives_only_an_advisory_to_d(self):
        res = run("single")
        d = main_decision(res)
        warn, _ = res.targets[d.hazard_id]
        self.assertEqual([(t.vehicle_id, t.level) for t in warn], [("D", "advisory")])

    def test_duplicates_merge_into_one_hazard_and_warn_only_d(self):
        res = run("duplicates")
        self.assertEqual(len(res.report.decisions), 1)
        d = main_decision(res)
        self.assertEqual(d.independent_sources, 3)
        warn, skip = res.targets[d.hazard_id]
        self.assertEqual([(t.vehicle_id, t.level) for t in warn], [("D", "warning")])
        self.assertIn(("F", "opposite direction"), [(s.vehicle_id, s.reason) for s in skip])

    def test_false_report_is_outvoted_by_negative_evidence_and_costs_reputation(self):
        res = run("false_report")
        d = main_decision(res)
        self.assertEqual({e.source for e in d.contra}, {"B", "C"})
        self.assertLess(res.engine.rep.score("A"), 6 / 13)  # below its starting score
        self.assertEqual(res.targets[d.hazard_id], ([], []))

    def test_sybil_identities_collapse_into_one_source(self):
        res = run("sybil")
        d = main_decision(res)
        self.assertEqual(d.independent_sources, 1)
        self.assertTrue(d.sybil_suspected)
        self.assertEqual(sorted(d.groups[0]), ["S1", "S2", "S3", "S4", "S5"])

    def test_sybil_would_win_if_identities_were_counted_separately(self):
        """Shows the independence check is doing real work, not the thresholds alone."""
        res = run("sybil")
        d = main_decision(res)
        naive = sum(e.weight for e in d.support) - res.engine.cfg.lam * sum(e.weight for e in d.contra)
        self.assertGreater(naive, res.engine.cfg.unconfirmed_net)

    def test_cloud_outage_does_not_change_the_decision(self):
        online, offline = run("duplicates"), run("cloud_outage")
        self.assertEqual([d.state for d in online.report.decisions], [d.state for d in offline.report.decisions])
        self.assertEqual(offline.cloud.stored, [])
        self.assertGreater(len(offline.cloud.queue), 0)
        self.assertEqual(offline.cloud.reconnect(), len(online.cloud.stored))

    def test_replayed_old_report_is_rejected(self):
        res = run("replay")
        self.assertEqual(len(res.report.rejected), 1)
        self.assertIn("stale", res.report.rejected[0].reasons[0])

    def test_gps_spoof_is_downgraded_for_position_mismatch(self):
        res = run("gps_spoof")
        d = main_decision(res)
        self.assertTrue(any("sender's own position" in r for r in d.reasons))
        self.assertLess(d.support[0].trust.g, 0.5)


if __name__ == "__main__":
    unittest.main()
