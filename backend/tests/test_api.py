"""Backend API tests: the report path (verification order, idempotent retries, replay), device lifecycle,
hazard queries and reputation updates. Run:  cd backend && python3 -m unittest -v"""
import json
import struct
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from icvsp_backend.app import create_app
from icvsp_backend.store import Store
from icvsp.model import to_iso

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = (ROOT / "verification/golden/device_reports_v2.jsonl").read_text().splitlines()
DEMO_KEY = json.loads(GOLDEN[0])["public_key"]
T0 = 1790000000.0                  # 2026-09-21, the time of the golden reports
ADMIN = {"X-Admin-Token": "test-admin"}


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Car:
    """Signs reports in the device format (same signed-bytes layout as firmware/lib/icvsp_core/src/signer.h)."""

    def __init__(self, device_id: str, boot: int = 1):
        self.id, self.boot, self.seq = device_id, boot, 0
        self.key = Ed25519PrivateKey.generate()
        self.public_hex = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def report(self, t: float, lat: float, lon: float, type_="pothole", heading=90.0, conf=0.9, **overrides) -> bytes:
        self.seq += 1
        ev = {"schema_version": 2, "event_id": f"{self.id}-{self.boot}-{self.seq}", "device_id": self.id,
              "boot_id": self.boot, "sequence": self.seq, "type": type_, "detected_at": to_iso(t),
              "gps_valid": True, "latitude": lat, "longitude": lon, "position_accuracy_m": 3.5,
              "speed_kmh": 40.0, "heading_deg": heading, "severity": "high", "detection_confidence": conf,
              "model": "imu-v0"}
        ev.update(overrides)
        return self.sign(ev, int(t * 1000))

    def sign(self, ev: dict, utc_ms: int) -> bytes:
        payload = json.dumps(ev, separators=(",", ":"))
        msg = b"ICVSP-v1\0" + self.id.encode() + b"\0" + ev["event_id"].encode() + b"\0" + struct.pack(">q", utc_ms)
        sig = self.key.sign(msg + payload.encode()).hex()
        return (f'{{"event":{payload},"sender":"{self.id}","msg_id":"{ev["event_id"]}","utc_ms":{utc_ms},'
                f'"public_key":"{self.public_hex}","signature":"{sig}"}}').encode()


def east(lat: float, lon: float, metres: float) -> tuple[float, float]:
    from icvsp.geo import offset
    return offset(lat, lon, 0.0, metres)


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(T0 + 60)
        self.store = Store()
        self.client = TestClient(create_app(self.store, self.clock, admin_token="test-admin"))

    def tearDown(self):
        self.store.close()

    def register(self, device_id, key, vetted=False):
        r = self.client.post("/devices", json={"device_id": device_id, "public_key": key, "vetted": vetted},
                             headers=ADMIN)
        self.assertEqual(r.status_code, 201, r.text)

    def post(self, line: bytes):
        return self.client.post("/reports", content=line, headers={"Content-Type": "application/json"})


class Reports(Base):
    def test_golden_device_report_is_accepted(self):
        """Contract: the C++ device's own output is accepted unchanged (format v2, Ed25519)."""
        self.register("demo-car-01", DEMO_KEY)
        for line in GOLDEN:
            r = self.post(line.encode())
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["status"], "accepted")

    def test_unknown_device_is_rejected(self):
        self.assertEqual(self.post(GOLDEN[0].encode()).status_code, 403)

    def test_revoked_device_is_rejected(self):
        self.register("demo-car-01", DEMO_KEY)
        self.assertEqual(self.client.post("/devices/demo-car-01/revoke", headers=ADMIN).status_code, 200)
        r = self.post(GOLDEN[0].encode())
        self.assertEqual(r.status_code, 403)
        self.assertIn("revoked", r.json()["detail"])

    def test_tampered_report_fails_the_signature(self):
        self.register("demo-car-01", DEMO_KEY)
        line = GOLDEN[1].replace('"severity":"high"', '"severity":"low"').encode()
        self.assertEqual(self.post(line).status_code, 401)

    def test_key_in_the_message_is_not_trusted(self):
        """An attacker's own key in the message does not help: only the registered key counts."""
        self.register("car-a", Car("x").public_hex)
        attacker = Car("car-a")
        self.assertEqual(self.post(attacker.report(T0, 30.0444, 31.2357)).status_code, 401)

    def test_retry_of_the_same_report_is_idempotent(self):
        self.register("demo-car-01", DEMO_KEY)
        self.assertEqual(self.post(GOLDEN[0].encode()).json()["status"], "accepted")
        r = self.post(GOLDEN[0].encode())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["status"], "duplicate")
        self.assertEqual(len(self.store.reports_since(0)), 1)

    def test_retry_with_a_different_line_ending_is_still_a_duplicate(self):
        """Found by the live-server check: a phone that adds a newline on retry must not get a conflict."""
        self.register("demo-car-01", DEMO_KEY)
        self.assertEqual(self.post(GOLDEN[0].encode()).json()["status"], "accepted")
        for variant in (GOLDEN[0] + "\n", GOLDEN[0] + "\r\n", "  " + GOLDEN[0]):
            r = self.post(variant.encode())
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["status"], "duplicate")

    def test_repeated_boot_and_sequence_is_rejected(self):
        car = Car("car-a")
        self.register("car-a", car.public_hex)
        self.assertEqual(self.post(car.report(T0, 30.0444, 31.2357)).status_code, 201)
        car.seq = 0  # same boot and sequence again, under a new event_id
        line = car.report(T0, 30.0444, 31.2357, event_id="car-a-1-1b")
        r = self.post(line)
        self.assertEqual(r.status_code, 409)
        self.assertIn("sequence 1", r.json()["detail"])

    def test_reused_event_id_with_other_content_is_a_conflict(self):
        car = Car("car-a")
        self.register("car-a", car.public_hex)
        self.post(car.report(T0, 30.0444, 31.2357))
        car.seq = 0
        self.assertEqual(self.post(car.report(T0 + 5, 30.0444, 31.2358)).status_code, 409)

    def test_malformed_body_is_rejected(self):
        self.assertEqual(self.post(b'{"hello": 1}').status_code, 400)
        self.assertEqual(self.post(b"\xff\xfe").status_code, 400)

    def test_oversized_body_is_rejected(self):
        self.assertEqual(self.post(b"x" * 5000).status_code, 413)

    def test_signed_but_invalid_event_is_rejected_by_schema(self):
        car = Car("car-a")
        self.register("car-a", car.public_hex)
        r = self.post(car.report(T0, 30.0444, 31.2357, detection_confidence=1.7))
        self.assertEqual(r.status_code, 422)
        self.assertIn("detection_confidence", r.json()["detail"])

    def test_envelope_and_event_must_name_the_same_device(self):
        car = Car("car-a")
        self.register("car-a", car.public_hex)
        self.assertEqual(self.post(car.report(T0, 30.0444, 31.2357, device_id="car-b")).status_code, 422)

    def test_every_rejection_is_logged_with_a_reason(self):
        self.post(GOLDEN[0].encode())
        rows = self.store.rejections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "unknown device")


class Devices(Base):
    def test_registration_needs_the_admin_token(self):
        r = self.client.post("/devices", json={"device_id": "x", "public_key": DEMO_KEY})
        self.assertEqual(r.status_code, 401)

    def test_bad_key_and_double_registration_are_refused(self):
        self.assertEqual(self.client.post("/devices", json={"device_id": "x", "public_key": "abc"},
                                          headers=ADMIN).status_code, 400)
        self.register("x", DEMO_KEY)
        self.assertEqual(self.client.post("/devices", json={"device_id": "x", "public_key": DEMO_KEY},
                                          headers=ADMIN).status_code, 409)


class Hazards(Base):
    LAT, LON = 30.0444, 31.2357

    def drive(self, vid, t0, start_m, speed=11.0, heading=90.0, seconds=60):
        """1 Hz beacons of a car driving east (heading 90) or west (270) through the hazard area."""
        rows = []
        for k in range(seconds):
            along = start_m + (speed * k if heading == 90.0 else -speed * k)
            lat, lon = east(self.LAT, self.LON, along)
            rows.append({"vehicle_id": vid, "t": t0 + k, "lat": lat, "lon": lon, "speed_mps": speed,
                         "heading_deg": heading})
        self.assertEqual(self.client.post("/beacons", json=rows).status_code, 200)

    def two_reports(self, vetted, **report):
        cars = [Car("car-a"), Car("car-b")]
        for i, c in enumerate(cars):
            self.register(c.id, c.public_hex, vetted=vetted)
            t = T0 + 30 * i
            self.drive(c.id, t - 10, -110)   # passes the hazard (at 0 m) about 10 s after t - 10
            self.assertEqual(self.post(c.report(t, self.LAT, self.LON, **report)).status_code, 201)
        self.clock.t = T0 + 60

    def test_minor_hazard_is_not_shown_and_severity_is_reported(self):
        """Severity scope: two trusted cars that both measured a small jolt do not make a hazard worth showing."""
        self.two_reports(vetted=True, severity="low", imu_peak_g=0.2)
        self.assertEqual(self.client.get("/hazards", params={"lat": self.LAT, "lon": self.LON}).json(), [])
        ov = self.client.get("/admin/overview", headers=ADMIN).json()
        self.assertEqual([(h["state"], h["severity"]) for h in ov["hazards"]], [("suppressed", "low")])

    def test_two_vetted_cars_give_a_warning_ahead_only(self):
        self.two_reports(vetted=True)
        ahead_lat, ahead_lon = east(self.LAT, self.LON, -200)
        hz = self.client.get("/hazards", params={"lat": ahead_lat, "lon": ahead_lon, "heading_deg": 90}).json()
        self.assertEqual(len(hz), 1)
        self.assertEqual(hz[0]["level"], "warning")
        self.assertEqual(hz[0]["independent_sources"], 2)
        self.assertEqual(hz[0]["severity"], "high")
        # a car going the other way, or already past it, is not warned
        self.assertEqual(self.client.get("/hazards", params={"lat": ahead_lat, "lon": ahead_lon,
                                                             "heading_deg": 270}).json(), [])
        past_lat, past_lon = east(self.LAT, self.LON, 100)
        self.assertEqual(self.client.get("/hazards", params={"lat": past_lat, "lon": past_lon,
                                                             "heading_deg": 90}).json(), [])

    def test_two_new_cars_give_only_an_advisory(self):
        """Cold start at system level: new devices (reliability 0.25) cannot raise a warning together."""
        self.two_reports(vetted=False)
        hz = self.client.get("/hazards", params={"lat": self.LAT, "lon": self.LON, "radius_m": 300}).json()
        self.assertTrue(all(h["level"] == "advisory" for h in hz))

    def test_settle_rewards_confirmed_reports_once(self):
        self.two_reports(vetted=True)
        third = Car("car-c")
        self.register("car-c", third.public_hex, vetted=True)
        self.drive("car-c", T0 + 40, -110)
        self.post(third.report(T0 + 50, self.LAT, self.LON))
        self.clock.t = T0 + 120
        before = self.store.reputations()["car-a"]
        first = self.client.post("/admin/settle", headers=ADMIN).json()["updates"]
        second = self.client.post("/admin/settle", headers=ADMIN).json()["updates"]
        self.assertTrue(any("confirmed" in u for u in first))
        self.assertEqual(second, [])
        self.assertEqual(self.store.reputations()["car-a"][0], before[0] + 1.0)


    def test_overview_shows_decisions_reliability_positions_and_rejections(self):
        """The operator dashboard's data: every decision with its state and reasons, device reliability,
        live positions and the rejection log; admin only."""
        self.assertEqual(self.client.get("/admin/overview").status_code, 401)
        self.two_reports(vetted=True)
        self.post(GOLDEN[0].encode())                       # unknown device, logged
        ov = self.client.get("/admin/overview", headers=ADMIN).json()
        self.assertEqual(ov["counts"], {"devices": 2, "reports": 2, "rejections": 1})
        self.assertEqual({d["device_id"]: d["reliability"] for d in ov["devices"]}, {"car-a": 0.571, "car-b": 0.571})
        hz = ov["hazards"]
        self.assertEqual(len(hz), 1)
        self.assertEqual(hz[0]["state"], "corroborated")
        self.assertEqual(hz[0]["support"], ["car-a", "car-b"])
        self.assertTrue(hz[0]["reasons"])
        self.assertEqual(ov["rejections"][0]["reason"], "unknown device")
        self.assertEqual({v["vehicle_id"] for v in ov["vehicles"]}, {"car-a", "car-b"})
        self.assertEqual(self.client.get("/dashboard").status_code, 200)


if __name__ == "__main__":
    unittest.main()
