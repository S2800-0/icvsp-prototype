# ICVSP backend (prototype)

Receives the signed reports that phones forward from the in-vehicle devices, checks them, runs the trust
engine (`engine/`) and answers "which hazards are ahead of me?". FastAPI + SQLite for now; the storage layer
(`store.py`) is the only part that changes when the team moves to PostgreSQL (Supabase).

```bash
cd backend
python3 -m unittest -v                                          # 39 tests: API, sign-in and roles
ICVSP_ADMIN_TOKEN=dev SUPABASE_JWT_SECRET=<project JWT secret> uvicorn --factory icvsp_backend.app:create_app --reload
# interactive API docs: http://127.0.0.1:8000/docs
# operator dashboard:   http://127.0.0.1:8000/dashboard (asks for the admin token)
```

| Endpoint | What it does |
|---|---|
| `POST /devices` (admin) | Register a device and its Ed25519 public key (controlled provisioning, FR-SEC-03/04); `vetted` gives a known fleet device a starting history |
| `POST /devices/{id}/revoke` (admin) | Revoke a device; its reports are refused from then on (FR-SEC-12) |
| `POST /reports` | Body: the signed report line **exactly as the device sent it**. Checks, in order: size and format → device registered and active → signature with the *registered* key → schema v2 → envelope matches event → not a repeated event ID or boot/sequence. A retry of the same line is answered `duplicate` (safe for the phone's offline queue) |
| `POST /beacons` | 1 Hz positions from the phone (`vehicle_id` = device ID): needed for plausibility and "drove past and saw nothing" |
| `GET /hazards?lat=&lon=&heading_deg=&radius_m=` | Hazards ahead in the same direction: `advisory` (unconfirmed) or `warning` (corroborated / confirmed), with the engine's reasons |
| `GET /admin/overview` (admin) | Operator view: every hazard decision with its state and the engine's reasons, device reliability, live vehicle positions, latest reports and rejections. Used by `/dashboard` |
| `POST /admin/settle` (admin) | Updates device reputation from confident outcomes, each report and each pass-by counted once |

Every refused report is logged with its reason (`rejections` table).

## Sign-in and roles (FR-SEC-01/02/19/21, NFR-SEC-02)

The app signs in with Supabase Auth and sends `Authorization: Bearer <access token>`. `auth.py` checks the
token (HS256 JWT: signature, expiry, audience) and reads the role and vehicle from `app_metadata`:
`{"role": "driver" | "ambulance" | "admin", "vehicle_id": "<device id>"}`. `app_metadata` is used because only an
admin can change it (a user can edit `user_metadata`). The secret comes from `SUPABASE_JWT_SECRET`
(optional `SUPABASE_JWT_AUDIENCE`, default `authenticated`); with no secret set, every token is refused.

| Endpoint | Who |
|---|---|
| `GET /health`, `POST /reports` | anyone (reports are checked by the device signature) |
| `POST /beacons` | Driver or Ambulance, only for the vehicle in their token (otherwise 403) |
| `GET /hazards` | Driver or Ambulance |
| `POST /devices`, `POST /devices/{id}/revoke`, `GET /admin/overview`, `POST /admin/settle` | Administrator (Bearer token, or the legacy `X-Admin-Token` header, kept for the simulation and demo) |

No or invalid token: 401. Valid token, wrong role: 403. Ambulance has the same rights as Driver until the
security team decides otherwise. Every failure is logged on the `icvsp.security` logger (time, status, reason,
endpoint, client address), never the token, signature or password.

**Not done yet** (security V-cycle): signed backend → phone alerts (FR-SEC-15–17); rate limits (FR-SEC-20); TLS
comes from the hosting platform (FR-SEC-08). Reputation is updated by `POST /admin/settle`, to be run on a schedule.
