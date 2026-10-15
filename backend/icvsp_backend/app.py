"""HTTP API (FastAPI). Run locally:  cd backend && uvicorn --factory icvsp_backend.app:create_app --reload
Interactive API docs at http://127.0.0.1:8000/docs

Sign-in and roles (FR-SEC-01/02): see auth.py. Rate limits (FR-SEC-20): see ratelimit.py. Still open: signed
backend->phone alerts (FR-SEC-15-17); TLS comes from the hosting platform (FR-SEC-08)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .auth import ADMIN, AMBULANCE, DRIVER, Auth, Principal
from .ratelimit import Limit, RateLimiter, limits_from_env
from .service import Service
from .store import Store
from .verify import Rejected

DASHBOARD = Path(__file__).with_name("dashboard.html")


class DeviceIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    public_key: str
    vetted: bool = False


class BeaconIn(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=64)
    t: float                   # Unix seconds
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_mps: float = Field(ge=0)
    heading_deg: float
    detector_on: bool = True


def create_app(store: Store | None = None, clock: Callable[[], float] = time.time,
               admin_token: str | None = None, jwt_secret: str | None = None,
               rate_limits: dict[str, Limit | None] | None = None) -> FastAPI:
    """`rate_limits` overrides single limits (e.g. {"reports_ip": None} when all callers share one address)."""
    svc = Service(store or Store(os.environ.get("ICVSP_DB", "icvsp.sqlite3")))
    limiter = RateLimiter(limits_from_env(rate_limits), clock)
    auth = Auth(jwt_secret, admin_token, clock, limiter=limiter)
    is_admin = Depends(auth.require(ADMIN, admin_token_ok=True))      # Bearer admin, or legacy X-Admin-Token
    is_user = Depends(auth.require(DRIVER, AMBULANCE))                # Ambulance = Driver until decided

    def limited(kind: str, key: str) -> None:
        if (wait := limiter.hit(kind, key)) is not None:
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(wait) + 1)})

    def admin_limited(who: Principal = is_admin) -> Principal:
        limited("admin", who.user_id)
        return who
    is_admin = Depends(admin_limited)
    app = FastAPI(title="ICVSP backend (prototype)", version="0.1")
    app.state.service = svc

    def fail(r: Rejected):
        headers = {"Retry-After": str(int(r.retry_after) + 1)} if r.retry_after is not None else None
        raise HTTPException(r.status, r.reason, headers=headers)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/devices", status_code=201)
    def register(d: DeviceIn, _: Principal = is_admin):
        try:
            svc.register(d.device_id, d.public_key, clock(), d.vetted)
        except Rejected as r:
            fail(r)
        return {"device_id": d.device_id, "status": "ACTIVE"}

    @app.post("/devices/{device_id}/revoke")
    def revoke(device_id: str, _: Principal = is_admin):
        if not svc.store.set_status(device_id, "REVOKED"):
            raise HTTPException(404, "unknown device")
        return {"device_id": device_id, "status": "REVOKED"}

    @app.post("/reports", status_code=201)
    async def report(request: Request):
        """Body: the signed report line exactly as the device sent it over BLE."""
        limited("reports_ip", request.client.host if request.client else "-")
        try:
            return svc.submit(await request.body(), clock(), limit=lambda dev: limiter.hit("reports_device", dev))
        except Rejected as r:
            fail(r)

    @app.post("/beacons")
    def beacons(items: list[BeaconIn], request: Request, who: Principal = is_user):
        """A user may only send positions for their own vehicle (the token's vehicle_id)."""
        limited("beacons_user", who.user_id)
        if any(b.vehicle_id != who.vehicle_id for b in items):
            auth.deny(request, 403, "vehicle does not belong to this user")
        return {"stored": svc.add_beacons([b.model_dump() for b in items])}

    @app.get("/hazards")
    def hazards(lat: float, lon: float, radius_m: float = 1000.0, heading_deg: float | None = None,
               who: Principal = is_user):
        limited("hazards_user", who.user_id)
        return svc.hazards(clock(), lat, lon, radius_m, heading_deg)

    @app.get("/admin/overview")
    def overview(recent: int = 30, _: Principal = is_admin):
        """Operator view used by the dashboard (/dashboard)."""
        return svc.overview(clock(), max(1, min(recent, 200)))

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return DASHBOARD.read_text()

    @app.post("/admin/settle")
    def settle(_: Principal = is_admin):
        return {"updates": svc.settle(clock())}

    return app

