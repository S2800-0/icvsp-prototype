"""Component/system tests (V-cycle) of the simulated BLE link, carrying the REAL signed device reports.
Run:  python3 -m pytest sil/test_ble_link.py -v      (a few seconds)"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))
from ble_link import (BleConfig, BleLink, Reassembler, fragment, fragment_count,  # noqa: E402
                      report_loss_probability)
from icvsp_backend.verify import parse, verify_signature  # noqa: E402

REPORTS = [l.rstrip("\n").encode() for l in open(ROOT / "verification/golden/device_reports_v2.jsonl") if l.strip()]


def signature_ok(raw: bytes) -> bool:
    env = parse(raw)
    verify_signature(env, __import__("json").loads(raw)["public_key"])   # raises Rejected if the bytes were changed
    return True


class Fragmenting(unittest.TestCase):
    def test_real_reports_need_many_fragments_at_the_default_mtu(self):
        self.assertTrue(all(650 < len(r) < 800 for r in REPORTS))
        n = len(REPORTS[0])
        self.assertEqual(fragment_count(n, 23), 45)       # 16 payload bytes per notification at MTU 23
        self.assertEqual(fragment_count(n, 185), 4)
        self.assertEqual(fragment_count(n, 247), 3)

    def test_roundtrip_with_reordered_and_duplicate_fragments(self):
        frags = fragment(7, REPORTS[0], 185)
        ra, out = Reassembler(), None
        for f in reversed(frags + frags[:1]):
            out = ra.push(0.0, f) or out
        self.assertEqual(out, REPORTS[0])

    def test_incomplete_message_is_dropped_after_the_timeout(self):
        frags = fragment(1, REPORTS[0], 185)
        ra = Reassembler(timeout_s=2.0)
        for f in frags[:-1]:
            self.assertIsNone(ra.push(0.0, f))
        self.assertIsNone(ra.push(10.0, frags[-1]))


class RealReportsSurviveTheLink(unittest.TestCase):
    def test_delivered_bytes_are_identical_and_the_signature_still_verifies(self):
        res = BleLink(BleConfig(fragment_loss=0.0), seed=1).simulate([(100.0, REPORTS[0]), (110.0, REPORTS[1])])
        self.assertFalse(res.lost)
        for d in res.deliveries:
            self.assertEqual(d.payload, REPORTS[d.index])
            self.assertTrue(signature_ok(d.payload))
            self.assertLess(d.t_arrival - d.t_sent, 0.2)

    def test_a_changed_fragment_breaks_the_signature(self):
        frags = fragment(3, REPORTS[0], 185)
        frags[1] = frags[1][:10] + bytes([frags[1][10] ^ 1]) + frags[1][11:]
        ra, out = Reassembler(), None
        for f in frags:
            out = ra.push(0.0, f) or out
        with self.assertRaises(Exception):
            signature_ok(out)

    def test_lost_ack_gives_duplicates_the_backend_must_ignore(self):
        res = BleLink(BleConfig(fragment_loss=0.0, ack_loss=1.0, max_attempts=3, ack_timeout_s=0.5), seed=5).simulate([(0.0, REPORTS[0])])
        self.assertEqual([d.duplicate for d in res.deliveries], [False, True, True])
        self.assertEqual(len({d.payload for d in res.deliveries}), 1)


class LossAndDelay(unittest.TestCase):
    def test_default_mtu_is_far_more_fragile_than_a_negotiated_mtu(self):
        small = report_loss_probability(0.02, fragment_count(len(REPORTS[0]), 23))
        big = report_loss_probability(0.02, fragment_count(len(REPORTS[0]), 247))
        self.assertAlmostEqual(small, 0.6, delta=0.05)
        self.assertAlmostEqual(big, 0.059, delta=0.01)

    def test_simulated_report_loss_matches_the_analytic_rate(self):
        msgs = [(i * 5.0, REPORTS[i % 2]) for i in range(4000)]
        res = BleLink(BleConfig(mtu=185, fragment_loss=0.05, max_attempts=1, ack_timeout_s=0.1), seed=3).simulate(msgs)
        expected = report_loss_probability(0.05, fragment_count(len(REPORTS[0]), 185))
        self.assertAlmostEqual(len(res.lost) / len(msgs), expected, delta=0.02)

    def test_retries_recover_most_losses_at_the_cost_of_the_ack_timeout(self):
        msgs = [(i * 10.0, REPORTS[0]) for i in range(2000)]
        one = BleLink(BleConfig(fragment_loss=0.05, max_attempts=1), seed=4).simulate(msgs)
        three = BleLink(BleConfig(fragment_loss=0.05, max_attempts=3), seed=4).simulate(msgs)
        self.assertLess(len(three.lost), 0.2 * len(one.lost))
        late = [d for d in three.deliveries if d.attempt > 1]
        self.assertTrue(late and all(d.t_arrival - d.t_sent >= 1.0 for d in late))


class LinkDrop(unittest.TestCase):
    def test_reports_are_buffered_in_order_and_flushed_after_reconnect(self):
        cfg = BleConfig(fragment_loss=0.0, disconnects=((100.0, 160.0),), queue_limit=16)
        res = BleLink(cfg, seed=6).simulate([(90.0, REPORTS[0]), (110.0, REPORTS[1]), (130.0, REPORTS[0]), (200.0, REPORTS[1])])
        self.assertFalse(res.lost)
        self.assertEqual([d.index for d in res.deliveries], [0, 1, 2, 3])
        self.assertTrue(all(d.t_arrival >= 160.0 for d in res.deliveries if d.index in (1, 2)))

    def test_queue_overflow_drops_the_oldest_reports(self):
        cfg = BleConfig(fragment_loss=0.0, disconnects=((0.0, 100.0),), queue_limit=2)
        res = BleLink(cfg, seed=7).simulate([(float(t), REPORTS[0]) for t in (10, 20, 30, 40)])
        self.assertEqual(sorted(l.index for l in res.lost), [0, 1])
        self.assertEqual([d.index for d in res.deliveries], [2, 3])

    def test_same_seed_gives_the_same_run(self):
        msgs = [(i * 3.0, REPORTS[i % 2]) for i in range(200)]
        a = BleLink(BleConfig(fragment_loss=0.1), seed=9).simulate(msgs)
        b = BleLink(BleConfig(fragment_loss=0.1), seed=9).simulate(msgs)
        self.assertEqual([(d.index, round(d.t_arrival, 6)) for d in a.deliveries], [(d.index, round(d.t_arrival, 6)) for d in b.deliveries])


class WholeChainOverFragmentedBle(unittest.TestCase):
    """The simulation's opt-in scenario: real device code -> fragmented BLE -> backend API (signatures, replay)."""

    @classmethod
    def setUpClass(cls):
        import run_sil
        run_sil.build()
        scn = run_sil.SCENARIOS["ble_fragmented"]
        scn.cars = 16
        scn.minutes = 12
        cls.r = run_sil.simulate(scn, seed=5)

    def test_reports_reach_the_backend_and_none_is_corrupted(self):
        rep = self.r["reports"]
        self.assertGreater(rep["accepted"], 0)
        self.assertEqual(rep["rejected"], {})             # fragments are re-assembled byte for byte: no signature failures

    def test_lost_ack_duplicates_are_recognised_not_double_counted(self):
        self.assertGreaterEqual(self.r["reports"]["duplicate"], 1)


if __name__ == "__main__":
    unittest.main()
