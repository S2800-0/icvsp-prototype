"""Sign-in and roles tests (FR-SEC-01/02/19/21, NFR-SEC-02). Tokens are made here with a test secret,
so no internet and no real Supabase account is needed. Run:  cd backend && python3 -m unittest -v"""
import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from icvsp_backend.app import create_app
from icvsp_backend.store import Store

SECRET = "test-only-secret-not-real"
T0 = 1790000060.0
ADMIN_HEADER = {"X-Admin-Token": "test-admin"}


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(role="driver", vehicle="car-1", sub="user-1", secret=SECRET, exp=T0 + 3600, alg="HS256",
               aud="authenticated", **extra) -> str:
    meta = {k: v for k, v in (("role", role), ("vehicle_id", vehicle)) if v is not None}
    claims = {"sub": sub, "aud": aud, "role": "authenticated", "app_metadata": meta, **extra}
    if exp is not None:
        claims["exp"] = exp
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()) if alg != "none" else ""
    return f"{head}.{body}.{sig}"


def bearer(**kw) -> dict:
    return {"Authorization": f"Bearer {make_token(**kw)}"}


def beacon(vehicle="car-1") -> dict:
    return {"vehicle_id": vehicle, "t": T0, "lat": 30.0, "lon": 31.0, "speed_mps": 10.0, "heading_deg": 90.0}


HAZ = {"lat": 30.0, "lon": 31.0}


class AuthBase(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.client = TestClient(create_app(self.store, lambda: T0, admin_token="test-admin",
                                            jwt_secret=SECRET))

    def tearDown(self):
        self.store.close()


class SignIn(AuthBase):
    def test_no_token_gives_401_on_protected_endpoints(self):
        for call in (lambda: self.client.post("/beacons", json=[beacon()]),
                     lambda: self.client.get("/hazards", params=HAZ),
                     lambda: self.client.get("/admin/overview"),
                     lambda: self.client.post("/admin/settle"),
                     lambda: self.client.post("/devices", json={"device_id": "x", "public_key": "00"}),
                     lambda: self.client.post("/devices/x/revoke")):
            self.assertEqual(call().status_code, 401)

    def test_invalid_token_gives_401(self):
        bad = {"garbage": "not-a-jwt",
               "wrong secret": make_token(secret="another-secret"),
               "alg none": make_token(alg="none"),
               "wrong audience": make_token(aud="something-else"),
               "no expiry": make_token(exp=None)}
        for name, tok in bad.items():
            r = self.client.get("/hazards", params=HAZ, headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(r.status_code, 401, name)

    def test_expired_token_gives_401(self):
        r = self.client.get("/hazards", params=HAZ, headers=bearer(exp=T0 - 1))
        self.assertEqual(r.status_code, 401)

    def test_no_secret_configured_refuses_every_token(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_JWT_SECRET", None)
            store = Store()
            self.addCleanup(store.close)
            client = TestClient(create_app(store, lambda: T0, admin_token="test-admin"))
        self.assertEqual(client.get("/hazards", params=HAZ, headers=bearer()).status_code, 401)

    def test_secret_is_read_from_the_environment(self):
        with mock.patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "env-secret"}):
            store = Store()
            self.addCleanup(store.close)
            client = TestClient(create_app(store, lambda: T0, admin_token="test-admin"))
        ok = client.get("/hazards", params=HAZ, headers=bearer(secret="env-secret"))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(client.get("/hazards", params=HAZ, headers=bearer()).status_code, 401)

    def test_health_and_reports_do_not_need_a_user_token(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        # /reports is checked by the device signature, not a user token: a bad body is refused for that
        # reason (400-class), never 401/403 from the sign-in check.
        r = self.client.post("/reports", content=b"{}", headers={"Content-Type": "application/json"})
        self.assertNotIn(r.status_code, (401, 403))


class Roles(AuthBase):
    def test_driver_gets_403_on_administrator_endpoints(self):
        h = bearer(role="driver")
        for call in (lambda: self.client.get("/admin/overview", headers=h),
                     lambda: self.client.post("/admin/settle", headers=h),
                     lambda: self.client.post("/devices", json={"device_id": "x", "public_key": "00"}, headers=h),
                     lambda: self.client.post("/devices/x/revoke", headers=h)):
            self.assertEqual(call().status_code, 403)

    def test_driver_and_ambulance_can_use_beacons_and_hazards(self):
        for role in ("driver", "ambulance"):
            h = bearer(role=role)
            self.assertEqual(self.client.post("/beacons", json=[beacon()], headers=h).status_code, 200, role)
            self.assertEqual(self.client.get("/hazards", params=HAZ, headers=h).status_code, 200, role)

    def test_administrator_can_use_administrator_endpoints(self):
        h = bearer(role="admin", vehicle=None)
        self.assertEqual(self.client.get("/admin/overview", headers=h).status_code, 200)
        self.assertEqual(self.client.post("/admin/settle", headers=h).status_code, 200)
        self.assertEqual(self.client.post("/devices/nobody/revoke", headers=h).status_code, 404)

    def test_administrator_is_not_a_driver(self):
        h = bearer(role="admin", vehicle=None)
        self.assertEqual(self.client.get("/hazards", params=HAZ, headers=h).status_code, 403)
        self.assertEqual(self.client.post("/beacons", json=[beacon()], headers=h).status_code, 403)

    def test_token_without_a_valid_role_gets_403(self):
        for role in (None, "superuser", "authenticated"):
            r = self.client.get("/hazards", params=HAZ, headers=bearer(role=role))
            self.assertEqual(r.status_code, 403, role)

    def test_legacy_admin_token_still_works_on_administrator_endpoints_only(self):
        self.assertEqual(self.client.get("/admin/overview", headers=ADMIN_HEADER).status_code, 200)
        self.assertEqual(self.client.post("/admin/settle", headers=ADMIN_HEADER).status_code, 200)
        self.assertEqual(self.client.get("/admin/overview", headers={"X-Admin-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/hazards", params=HAZ, headers=ADMIN_HEADER).status_code, 401)


class OwnVehicle(AuthBase):
    def test_driver_cannot_send_positions_for_another_vehicle(self):
        r = self.client.post("/beacons", json=[beacon("car-2")], headers=bearer(vehicle="car-1"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.beacons_since(0), [])

    def test_one_foreign_position_in_a_batch_refuses_the_whole_batch(self):
        r = self.client.post("/beacons", json=[beacon("car-1"), beacon("car-2")], headers=bearer(vehicle="car-1"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.beacons_since(0), [])

    def test_own_vehicle_is_stored(self):
        r = self.client.post("/beacons", json=[beacon("car-1")], headers=bearer(vehicle="car-1"))
        self.assertEqual(r.json(), {"stored": 1})

    def test_user_without_a_vehicle_cannot_send_positions(self):
        r = self.client.post("/beacons", json=[beacon("car-1")], headers=bearer(vehicle=None))
        self.assertEqual(r.status_code, 403)


class Logging(AuthBase):
    def test_failed_sign_in_is_logged_without_the_token(self):
        token = make_token(secret="another-secret")
        with self.assertLogs("icvsp.security", level="WARNING") as cm:
            self.client.get("/hazards", params=HAZ, headers={"Authorization": f"Bearer {token}"})
        text = "\n".join(cm.output)
        self.assertIn("reason=bad signature", text)
        self.assertIn("ts=", text)
        self.assertIn("/hazards", text)
        for secret in (token, token.split(".")[2], SECRET, "test-admin"):
            self.assertNotIn(secret, text)

    def test_wrong_role_and_missing_token_are_logged_with_a_reason(self):
        with self.assertLogs("icvsp.security", level="WARNING") as cm:
            self.client.get("/admin/overview")
            self.client.get("/admin/overview", headers=bearer(role="driver"))
        text = "\n".join(cm.output)
        self.assertIn("reason=missing token", text)
        self.assertIn("reason=role not allowed", text)


if __name__ == "__main__":
    unittest.main()
