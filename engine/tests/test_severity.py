"""Severity scope: only hazards big enough to make a driver stop, slow down or swerve are warned about."""
import dataclasses
import unittest

from icvsp.config import Config
from icvsp.model import HazardState
from icvsp.pipeline import evaluate_traces
from icvsp.sim import SCENARIOS, run

WARNING = (HazardState.CORROBORATED, HazardState.CONFIRMED)


def decide(key, severity, imu_peak_g=None, only=None, cfg=None):
    """Runs a scripted scenario with every report (or only the reports of `only`) set to this severity."""
    scn = SCENARIOS[key]
    out = run(scn, 7)
    out.events = [dataclasses.replace(e, severity=severity, imu_peak_g=imu_peak_g)
                  if only is None or e.source in only else e for e in out.events]
    return evaluate_traces(scn, out, cfg or Config()).report.decisions[0]


class SeverityScope(unittest.TestCase):
    def test_well_reported_severe_hazard_still_warns(self):
        d = decide("duplicates", "medium", imu_peak_g=0.5)
        self.assertIn(d.state, WARNING)
        self.assertEqual(d.severity, "medium")

    def test_minor_hazard_never_warns_however_many_cars_report_it(self):
        d = decide("duplicates", "low", imu_peak_g=0.25)
        self.assertEqual(d.state, HazardState.SUPPRESSED)
        self.assertEqual(d.severity, "low")
        self.assertTrue(any("below medium" in r for r in d.reasons))

    def test_camera_only_reports_give_an_advisory_at_most(self):
        d = decide("duplicates", "low", imu_peak_g=None)
        self.assertEqual(d.state, HazardState.UNCONFIRMED)
        self.assertEqual(d.severity, "unknown")
        self.assertTrue(any("camera only" in r for r in d.reasons))

    def test_one_measured_severe_report_lets_camera_corroboration_count(self):
        # reporters A, B, C: B and C camera only, A keeps its high-severity (measured) report
        d = decide("duplicates", "low", imu_peak_g=None, only={"B", "C"})
        self.assertIn(d.state, WARNING)
        self.assertEqual(d.severity, "high")

    def test_lone_minor_report_gets_no_cold_start_advisory(self):
        d = decide("single", "low", imu_peak_g=0.25)
        self.assertEqual(d.state, HazardState.SUPPRESSED)

    def test_threshold_is_configurable(self):
        d = decide("duplicates", "medium", imu_peak_g=0.5, cfg=Config(warn_min_severity="high"))
        self.assertEqual(d.state, HazardState.SUPPRESSED)


if __name__ == "__main__":
    unittest.main()
