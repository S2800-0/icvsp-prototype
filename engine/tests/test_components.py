import math
import tempfile
import unittest

from icvsp import tracefile
from icvsp.config import Config
from icvsp.consensus import Engine
from icvsp.geo import distance_m, heading_diff, offset
from icvsp.model import Beacon, SafetyEvent
from icvsp.sim import BASE_T, SCENARIOS, run
from icvsp.trust import Reputation, freshness
from icvsp.validation import plausibility, schema_errors

CFG = Config()


def event(**kw) -> SafetyEvent:
    base = dict(event_id="e1", type="pothole", t=BASE_T, lat=30.0444, lon=31.2357, acc_m=4.0,
                road_segment="seg_1", speed_kmh=60, heading_deg=90, severity="high", confidence=0.9, source="A")
    base.update(kw)
    return SafetyEvent(**base)


def beacon_at(ev: SafetyEvent, dt: float = 0.0, **kw) -> Beacon:
    return Beacon(kw.get("vid", ev.source), ev.t + dt, kw.get("lat", ev.lat), kw.get("lon", ev.lon), 16.7, 90)


class Geometry(unittest.TestCase):
    def test_offset_and_distance_agree(self):
        lat, lon = offset(30.0, 31.0, 30.0, 40.0)
        self.assertAlmostEqual(distance_m(30.0, 31.0, lat, lon), 50.0, delta=0.1)

    def test_heading_diff_wraps(self):
        self.assertEqual(heading_diff(350, 10), 20)
        self.assertEqual(heading_diff(90, 270), 180)


class Schema(unittest.TestCase):
    def test_valid_event_has_no_errors(self):
        self.assertEqual(schema_errors(event().to_dict()), [])

    def test_missing_and_bad_fields_are_reported(self):
        d = event().to_dict()
        del d["source"]
        d["confidence"] = 1.7
        d["extra"] = 1
        errs = schema_errors(d)
        self.assertTrue(any("source" in e for e in errs))
        self.assertTrue(any("extra" in e for e in errs))

    def test_malformed_message_never_reaches_scoring(self):
        eng = Engine(CFG)
        d = event().to_dict()
        d["confidence"] = "high"
        self.assertFalse(eng.ingest_dict(d))
        rep = eng.decide(BASE_T)
        self.assertEqual(rep.decisions, [])
        self.assertEqual(len(rep.rejected), 1)


class Plausibility(unittest.TestCase):
    def test_future_timestamp_rejected(self):
        ev = event(t=BASE_T + 60)
        self.assertEqual(plausibility(ev, [], BASE_T, CFG).verdict, "reject")

    def test_poor_gps_rejected_and_mediocre_gps_downgraded(self):
        self.assertEqual(plausibility(event(acc_m=50), [], BASE_T, CFG).verdict, "reject")
        ev = event(acc_m=15)
        chk = plausibility(ev, [beacon_at(ev)], BASE_T, CFG)
        self.assertEqual(chk.verdict, "downgrade")
        self.assertTrue(0.2 < chk.consistency < 1.0)

    def test_report_far_from_own_position_downgraded(self):
        ev = event()
        far_lat, far_lon = offset(ev.lat, ev.lon, 0, 300)
        chk = plausibility(ev, [beacon_at(ev, lat=far_lat, lon=far_lon)], BASE_T, CFG)
        self.assertAlmostEqual(chk.consistency, 0.3)

    def test_teleporting_trace_downgraded(self):
        ev = event()
        jump_lat, jump_lon = offset(ev.lat, ev.lon, 0, 500)
        trace = [beacon_at(ev, -1), Beacon("A", ev.t, jump_lat, jump_lon, 16.7, 90), beacon_at(ev, 1)]
        self.assertLess(plausibility(ev, trace, BASE_T, CFG).consistency, 0.5)

    def test_duplicate_event_id_rejected(self):
        eng = Engine(CFG)
        ev = event()
        eng.ingest_beacon(beacon_at(ev))
        eng.ingest_event(ev)
        eng.ingest_event(ev)
        rep = eng.decide(BASE_T + 1)
        self.assertEqual(len(rep.decisions), 1)
        self.assertIn("duplicate", rep.rejected[0].reasons[0])


class TrustMath(unittest.TestCase):
    def test_beta_reputation(self):
        rep = Reputation(1, 3)
        self.assertAlmostEqual(rep.score("new"), 0.25)
        rep.seed("A", 20, 2)
        self.assertAlmostEqual(rep.score("A"), 21 / 26)
        before, after = rep.record("A", good=False)
        self.assertLess(after, before)

    def test_freshness_decays(self):
        tau = CFG.freshness_tau_s["pothole"]
        self.assertAlmostEqual(freshness(0, "pothole", CFG), 1.0)
        self.assertAlmostEqual(freshness(tau, "pothole", CFG), math.exp(-1))


class TraceFormat(unittest.TestCase):
    def test_export_and_reload_gives_the_same_decision(self):
        out = run(SCENARIOS["duplicates"])
        with tempfile.TemporaryDirectory() as tmp:
            tracefile.write(tmp, out.beacons, out.events)
            beacons, events = tracefile.read(tmp)
        self.assertEqual(len(beacons), len(out.beacons))
        eng = Engine(CFG)
        for vid, (g, b) in out.histories.items():
            eng.rep.seed(vid, g, b)
        for b in beacons:
            eng.ingest_beacon(b)
        for e in events:
            eng.ingest_dict(e)
        self.assertEqual(eng.decide(out.now).decisions[0].state.value, "confirmed")


if __name__ == "__main__":
    unittest.main()
