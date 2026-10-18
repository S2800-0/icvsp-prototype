# Verification (V-cycle, right side)

| File | What it is |
|---|---|
| `requirements.csv` | Requirement IDs from the system V-cycle workbook v1.1 (FR / NFR) and the security V-cycle (FR-SEC / NFR-SEC / SEC-EXT) |
| `traceability.csv` | Every test, integration check, experiment and inspection, its V-level and the requirements it covers (T- engine, F- firmware, B- backend, S- system simulation, IT- integration, E- experiment, I- inspection) |
| `golden/device_reports_v2.jsonl` | Signed reports produced by the device code: the contract between device, phone app and backend (the backend must accept them unchanged) |
| `check_traceability.py` | Reads the test results (JUnit XML), fails if a test is missing from the list, a listed test is gone or failed, or a requirement ID is unknown; writes the requirement → test → result report |
| `run_all.sh` | Runs every level locally exactly as CI does; evidence in `verification/out/` |

A requirement counts as **covered** only when it is implemented, integrated, tested and passed: all its automated
tests pass and at least one runs at integration or system level.

| V-level | What | Tool | CI job |
|---|---|---|---|
| Unit | device firmware (C++) | PlatformIO + Unity, native | Unit: device firmware |
| Unit / component | trust engine | pytest (+ coverage) | Unit + component: trust engine |
| Integration | backend API: signatures, registry, replay, sign-in, roles, rate limits | pytest + FastAPI test client (+ coverage) | Integration: backend API |
| Integration | device-signed reports checked by an independent Ed25519 library | `firmware/tools/check_with_engine.py` | Integration: device reports -> engine |
| System | whole chain in simulation (real device code, real backend, faults, attackers) | pytest, `sil/test_sil.py` | System: whole chain in simulation |
| Traceability | requirement → test → result | `check_traceability.py` | Traceability (+ "Test results" check) |

GitHub Actions (`.github/workflows/verification.yml`) runs one job per level on every push, publishes all results
as a "Test results" check, and puts code coverage and the traceability report on the run's summary page.
When you add a test, add a row to `traceability.csv`.

Run it locally:

```bash
verification/run_all.sh        # then open verification/out/coverage/index.html
```

In VS Code, open `ICVSP.code-workspace`: the Testing panel lists every Python test (run, debug, run with coverage),
and the PlatformIO panel runs the firmware tests (native > Advanced > Test). Each suite also still runs with plain
`python3 -m unittest -v` from its own folder.
