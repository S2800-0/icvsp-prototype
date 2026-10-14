"""HTTP API (FastAPI). Run locally:  cd backend && uvicorn --factory icvsp_backend.app:create_app --reload
Interactive API docs at http://127.0.0.1:8000/docs

Prototype limits (security V-cycle items still open): no user authentication yet (FR-SEC-01/02), so
beacons are not tied to a signed-in phone; device registration is protected only by an admin token;
TLS comes from the hosting platform (FR-SEC-08)."""
from __future__ import annotations

import hmac
import os
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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
               admin_token: str | None = None) -> FastAPI:
    svc = Service(store or Store(os.environ.get("ICVSP_DB", "icvsp.sqlite3")))
    token = admin_token if admin_token is not None else os.environ.get("ICVSP_ADMIN_TOKEN", "")
    app = FastAPI(title="ICVSP backend (prototype)", version="0.1")
    app.state.service = svc

    def admin(x_admin_token: str | None) -> None:
        if not token or not hmac.compare_digest(x_admin_token or "", token):
            raise HTTPException(401, "admin token required")

    def fail(r: Rejected):
        raise HTTPException(r.status, r.reason)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/devices", status_code=201)
    def register(d: DeviceIn, x_admin_token: str | None = Header(default=None)):
        admin(x_admin_token)
        try:
            svc.register(d.device_id, d.public_key, clock(), d.vetted)
        except Rejected as r:
            fail(r)
        return {"device_id": d.device_id, "status": "ACTIVE"}

    @app.post("/devices/{device_id}/revoke")
    def revoke(device_id: str, x_admin_token: str | None = Header(default=None)):
        admin(x_admin_token)
        if not svc.store.set_status(device_id, "REVOKED"):
            raise HTTPException(404, "unknown device")
        return {"device_id": device_id, "status": "REVOKED"}

    @app.post("/reports", status_code=201)
    async def report(request: Request):
        """Body: the signed report line exactly as the device sent it over BLE."""
        try:
            return svc.submit(await request.body(), clock())
        except Rejected as r:
            fail(r)

    @app.post("/beacons")
    def beacons(items: list[BeaconIn]):
        return {"stored": svc.add_beacons([b.model_dump() for b in items])}

    @app.get("/hazards")
    def hazards(lat: float, lon: float, radius_m: float = 1000.0, heading_deg: float | None = None):
        return svc.hazards(clock(), lat, lon, radius_m, heading_deg)

    @app.get("/admin/overview")
    def overview(recent: int = 30, x_admin_token: str | None = Header(default=None)):
        """Operator view used by the dashboard (/dashboard)."""
        admin(x_admin_token)
        return svc.overview(clock(), max(1, min(recent, 200)))

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return DASHBOARD.read_text()

    @app.post("/admin/settle")
    def settle(x_admin_token: str | None = Header(default=None)):
        admin(x_admin_token)
        return {"updates": svc.settle(clock())}

    return app

