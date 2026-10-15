"""Rate limit tests (FR-SEC-20, FR-SEC-21). Run:  cd backend && python3 -m unittest -v"""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from icvsp_backend.app import create_app
from icvsp_backend.ratelimit import Limit, RateLimiter, limits_from_env
from icvsp_backend.store import Store
from tests.test_api import JWT, T0, Car, Clock, user

ADMIN = {"X-Admin-Token": "test-admin"}


class Bucket(unittest.TestCase):
    def test_burst_then_steady_rate(self):
        clock = Clock(0.0)
        rl = RateLimiter({"k": Limit(per_min=60, burst=3)}, clock)
        self.assertEqual([rl.hit("k", "a") for _ in range(3)], [None, None, None])
        wait = rl.hit("k", "a")
        self.assertAlmostEqual(wait, 1.0, places=3)           # 60 per minute: one more after 1 s
        clock.t = 1.0
        self.assertIsNone(rl.hit("k", "a"))
        self.assertIsNone(rl.hit("k", "b"))                   # other keys are not affected

    def test_limits_are_configurable_from_the_environment(self):
        with mock.patch.dict(os.environ, {"ICVSP_RATE_REPORTS_DEVICE": "5/2", "ICVSP_RATE_HAZARDS_USER": "off"}):
            lim = limits_from_env()
        self.assertEqual(lim["reports_device"], Limit(5, 2))
        self.assertIsNone(lim["hazards_user"])


class Api(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(T0 + 60)
        self.store = Store()
        self.app_limits = {"reports_device": Limit(6, 2), "hazards_user": Limit(6, 2), "beacons_user": Limit(6, 2),
                           "reports_ip": Limit(600, 50), "auth_fail_ip": Limit(6, 3)}
        self.client = TestClient(create_app(self.store, self.clock, admin_token="test-admin", jwt_secret=JWT,
                                            rate_limits=self.app_limits))

    def tearDown(self):
        self.store.close()

    def post(self, line: bytes):
        return self.client.post("/reports", content=line, headers={"Content-Type": "application/json"})

    def register(self, car: Car):
        self.assertEqual(self.client.post("/devices", json={"device_id": car.id, "public_key": car.public_hex},
                                          headers=ADMIN).status_code, 201)

    def test_device_flooding_reports_gets_429_with_retry_after(self):
        car = Car("car-a")
        self.register(car)
        codes = [self.post(car.report(T0 + k, 30.0444, 31.2357 + k * 1e-3)).status_code for k in range(3)]
        self.assertEqual(codes, [201, 201, 429])
        r = self.post(car.report(T0 + 9, 30.0444, 31.2457))
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        self.clock.t += 60                                    # the allowance refills
        self.assertEqual(self.post(car.report(T0 + 10, 30.0444, 31.2467)).status_code, 201)

    def test_forged_reports_do_not_use_up_a_real_devices_allowance(self):
        victim, attacker = Car("car-a"), Car("car-a")         # attacker claims the victim's id with another key
        self.register(victim)
        for k in range(5):
            self.assertEqual(self.post(attacker.report(T0 + k, 30.0444, 31.2357)).status_code, 401)
        self.assertEqual(self.post(victim.report(T0, 30.0444, 31.2357)).status_code, 201)

    def test_rate_limited_report_is_logged_with_its_reason(self):
        car = Car("car-a")
        self.register(car)
        for k in range(3):
            self.post(car.report(T0 + k, 30.0444, 31.2357 + k * 1e-3))
        reasons = [r["reason"] for r in self.store.rejections()]
        self.assertIn("rate limit: too many reports from this device", reasons)

    def test_one_user_flooding_hazard_queries_gets_429_others_do_not(self):
        q = {"lat": 30.0444, "lon": 31.2357}
        codes = [self.client.get("/hazards", params=q, headers=user("car-a")).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(self.client.get("/hazards", params=q, headers=user("car-b")).status_code, 200)

    def test_one_user_flooding_positions_gets_429(self):
        row = [{"vehicle_id": "car-a", "t": T0, "lat": 30.0, "lon": 31.0, "speed_mps": 10.0, "heading_deg": 90.0}]
        codes = [self.client.post("/beacons", json=row, headers=user("car-a")).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_repeated_failed_sign_ins_are_slowed_down(self):
        bad = {"Authorization": "Bearer not.a.token"}
        codes = [self.client.get("/hazards", params={"lat": 30, "lon": 31}, headers=bad).status_code for _ in range(4)]
        self.assertEqual(codes, [401, 401, 401, 429])
        # even a valid token from that address waits until the allowance refills
        self.assertEqual(self.client.get("/hazards", params={"lat": 30, "lon": 31}, headers=user("car-a")).status_code, 429)
        self.clock.t += 60
        self.assertEqual(self.client.get("/hazards", params={"lat": 30, "lon": 31}, headers=user("car-a")).status_code, 200)

    def test_unsigned_junk_flood_is_stopped_by_address(self):
        client = TestClient(create_app(Store(), self.clock, admin_token="test-admin", jwt_secret=JWT,
                                       rate_limits={"reports_ip": Limit(6, 2)}))
        codes = [client.post("/reports", content=b"junk").status_code for _ in range(3)]
        self.assertEqual(codes, [400, 400, 429])


if __name__ == "__main__":
    unittest.main()
