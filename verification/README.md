# Verification (V-cycle, right side)

| File | What it is |
|---|---|
| `requirements.csv` | Requirement IDs from the system V-cycle workbook v1.1 (FR / NFR) and the security V-cycle (FR-SEC / NFR-SEC / SEC-EXT) |
| `traceability.csv` | Every test, integration check, experiment and inspection, its V-level and the requirements it covers (T- engine, F- firmware, B- backend, S- system simulation, IT- integration, E- experiment, I- inspection) |
| `golden/device_reports_v2.jsonl` | Signed reports produced by the device code: the contract between device, phone app and backend (the backend must accept them unchanged) |
| `check_traceability.py` | Reads the test logs, fails if a test is missing from the list, a listed test is gone or failed, or a requirement ID is unknown; writes the requirement → test → result report |

GitHub Actions (`.github/workflows/verification.yml`) runs all tests and this check on every push and puts the
report on the run's summary page. When you add a test, add a row to `traceability.csv`.

Run it locally:

```bash
(cd engine && python3 -m unittest -v 2> ../engine.log)
(cd backend && python3 -m unittest -v 2> ../backend.log)
python3 -m unittest sil/test_sil.py -v 2> sil.log
(cd firmware && pio test -e native -v > ../firmware.log && python3 tools/check_with_engine.py > ../integration.log)
python3 verification/check_traceability.py --unittest-log engine=engine.log --unittest-log backend=backend.log \
    --unittest-log sil=sil.log --firmware-log firmware.log --integration-log integration.log
```
