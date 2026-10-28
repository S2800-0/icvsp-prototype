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


    def test_parameters_are_validated(self):
        for bad in (dict(mtu=7), dict(mtu=3), dict(conn_interval_s=0), dict(notifs_per_event=0), dict(jitter_s=0.05),
                    dict(fragment_loss=1.5), dict(max_attempts=0), dict(queue_limit=0),
                    dict(disconnects=((5.0, 6.0), (1.0, 2.0)))):
            with self.assertRaises(ValueError):
                BleConfig(**bad)
        with self.assertRaises(ValueError):
            fragment_count(10_000, 23)                       # more than 255 fragments
        self.assertEqual(fragment_count(len(REPORTS[0]), 517), 2)   # value capped at the 512-byte ATT limit


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
    def test_default_mtu_is_far_more_fragile_than_a_negotiated_mtu(self):   # synthetic 2 % fragment erasure
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


    def test_partial_fragments_are_kept_across_attempts(self):
        cfg = BleConfig(mtu=185, fragment_loss=0.3, max_attempts=2, ack_timeout_s=0.5)
        res = BleLink(cfg, seed=8).simulate([(i * 10.0, REPORTS[0]) for i in range(3000)])
        delivered = len({d.index for d in res.deliveries}) / 3000
        n = fragment_count(len(REPORTS[0]), 185)
        self.assertAlmostEqual(delivered, (1 - 0.3 ** 2) ** n, delta=0.03)                  # about 0.69
        self.assertGreater(delivered, 1 - report_loss_probability(0.3, n, 2) + 0.15)        # not the 0.42 of fresh attempts


class Scheduling(unittest.TestCase):
    def test_simultaneous_reports_share_the_connection_event_capacity(self):
        cfg = BleConfig(mtu=185, fragment_loss=0.0, jitter_s=0.0, notifs_per_event=4)
        res = BleLink(cfg, seed=1).simulate([(0.0, REPORTS[0])] * 10)
        arrivals = [d.t_arrival for d in res.deliveries]
        self.assertEqual(arrivals, sorted(arrivals))
        self.assertGreaterEqual(max(arrivals), (10 * fragment_count(len(REPORTS[0]), 185) / 4 - 1) * cfg.conn_interval_s - 1e-9)
        self.assertGreaterEqual(len({round(a, 6) for a in arrivals}), 9)

    def test_arrivals_keep_the_send_order_and_time_never_goes_backwards(self):
        res = BleLink(BleConfig(mtu=23, fragment_loss=0.0, jitter_s=0.02), seed=2).simulate([(i * 0.05, REPORTS[i % 2]) for i in range(8)])
        self.assertEqual([d.index for d in res.deliveries], list(range(8)))
        self.assertTrue(all(a.t_arrival <= b.t_arrival for a, b in zip(res.deliveries, res.deliveries[1:])))

    def test_default_baseline_has_no_residual_loss(self):
        res = BleLink(BleConfig(), seed=9).simulate([(i * 2.0, REPORTS[i % 2]) for i in range(500)])
        self.assertFalse(res.lost)
        self.assertTrue(all(d.attempt == 1 for d in res.deliveries) and len(res.deliveries) == 500)


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

    def test_transmission_in_flight_when_the_link_drops_resumes_after_reconnect(self):
        cfg = BleConfig(mtu=23, fragment_loss=0.0, jitter_s=0.0, notifs_per_event=4, disconnects=((0.15, 5.0),))
        res = BleLink(cfg, seed=3).simulate([(0.0, REPORTS[0])])           # 45 fragments: the drop hits mid-message
        self.assertFalse(res.lost)
        self.assertEqual(res.deliveries[0].payload, REPORTS[0])
        self.assertGreaterEqual(res.deliveries[0].t_arrival, 5.0)

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
        import json
        import run_sil
        scn = run_sil.SCENARIOS["ble_fragmented"]
        run = run_sil.prepare(scn, seed=5)
        sent = [json.loads(p)["msg_id"] for _, k, p in run.events if k == "report"]
        distinct = len(set(sent))
        rep = self.r["reports"]
        self.assertGreater(len(sent), distinct)                      # copies exist (lost acks, phone upload resends)
        self.assertEqual(rep["accepted"], distinct)                   # each distinct report counted exactly once
        self.assertEqual(rep["duplicate"], len(sent) - distinct)      # every extra copy recognised as a duplicate


if __name__ == "__main__":
    unittest.main()
