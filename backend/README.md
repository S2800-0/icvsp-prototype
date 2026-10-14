# ICVSP backend (prototype)

Receives the signed reports that phones forward from the in-vehicle devices, checks them, runs the trust
engine (`engine/`) and answers "which hazards are ahead of me?". FastAPI + SQLite for now; the storage layer
(`store.py`) is the only part that changes when the team moves to PostgreSQL (Supabase).

```bash
cd backend
python3 -m unittest -v                                          # 21 API tests
ICVSP_ADMIN_TOKEN=dev uvicorn --factory icvsp_backend.app:create_app --reload
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

**Not done yet** (security V-cycle): user sign-in and roles (FR-SEC-01/02), so beacons are not yet tied to a
signed-in phone; signed backend → phone alerts (FR-SEC-15–17); rate limits (FR-SEC-20); TLS comes from the
hosting platform (FR-SEC-08). Reputation is updated by `POST /admin/settle`, to be run on a schedule.
