"""System test (V-cycle SYS.4/SYS.5) on a small simulation: the whole chain, with attackers.
Run:  python3 -m unittest sil/test_sil.py -v   (about a minute)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_sil  # noqa: E402


class WholeChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run_sil.build()
        scn = run_sil.SCENARIOS["attacks"]
        scn.cars = 24
        cls.r = run_sil.simulate(scn, seed=5)

    def test_real_hazards_produce_warnings_for_following_cars(self):
        self.assertGreater(self.r["warned_real_after_10min"], 0.3)

    def test_invented_hazards_never_reach_warning_level(self):
        self.assertEqual(self.r["warned_fake"], 0.0)

    def test_tampered_unknown_and_revoked_reports_are_refused(self):
        rej = self.r["reports"]["rejected"]
        self.assertIn("signature does not verify with the registered key", rej)
        self.assertIn("unknown device", rej)
        self.assertIn("device revoked", rej)

    def test_replayed_report_has_no_effect(self):
        self.assertGreaterEqual(self.r["reports"]["duplicate"], 1)

    def test_device_emits_within_the_fusion_window(self):
        self.assertLessEqual(self.r["device_delay_p95_s"], 3.5)


if __name__ == "__main__":
    unittest.main()
